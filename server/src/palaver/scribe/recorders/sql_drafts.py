import asyncio
import logging
import json
import time
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field, asdict, fields
from typing import Optional

import numpy as np
import soundfile as sf
from sqlmodel import SQLModel, Field, create_engine, Session, Relationship
from palaver_shared.audio_events import AudioEvent, AudioChunkEvent, AudioRingBuffer
from palaver.scribe.api import ScribeAPIListener
from palaver_shared.text_events import TextEvent
from palaver_shared.draft_events import DraftEvent, DraftStartEvent, DraftEndEvent, Draft, DraftRescanEvent

logger = logging.getLogger("SQLDraftRecorder")


class DraftRecord(SQLModel, table=True):
    """SQLModel for persisting draft information"""
    __tablename__ = "drafts"

    id: Optional[int] = Field(default=None, primary_key=True)
    draft_id: str = Field(index=True, unique=True)  # UUID for draft lookup
    timestamp: float  # Original draft timestamp
    start_text: str
    end_text: Optional[str] = None
    full_text: str
    classname: str
    directory_path: str  # Path to the draft-{timestamp} directory
    created_at: datetime = Field(default_factory=datetime.now)

    # Parent draft UUID for tracking revisions (e.g., rescans)
    parent_draft_id: Optional[str] = Field(default=None, index=True)

    @classmethod
    def create_with_parent(
        cls,
        session: Session,
        draft: Draft,
        parent_draft_uuid: str,
        directory_path: str = ""
    ) -> "DraftRecord":
        """Create a new DraftRecord with a parent relationship.

        Args:
            session: SQLModel Session for database operations
            draft: Draft dataclass instance to save
            parent_draft_uuid: The draft_id (UUID string) of the parent draft
            directory_path: Optional path to draft files directory

        Returns:
            The newly created DraftRecord instance

        Raises:
            ValueError: If parent draft not found
        """
        # Look up parent by draft_id (UUID) to validate it exists
        from sqlmodel import select
        statement = select(DraftRecord).where(DraftRecord.draft_id == parent_draft_uuid)
        parent_record = session.exec(statement).first()

        if not parent_record:
            raise ValueError(f"Parent draft not found: {parent_draft_uuid}")

        # Create new draft record with parent UUID
        draft_record = DraftRecord(
            draft_id=str(draft.draft_id),
            timestamp=draft.timestamp,
            start_text=draft.start_text,
            end_text=draft.end_text,
            full_text=draft.full_text,
            classname=str(draft.__class__),
            directory_path=directory_path,
            parent_draft_id=parent_draft_uuid
        )
        session.add(draft_record)
        session.commit()
        session.refresh(draft_record)

        logger.info(f"Created draft {draft_record.id} with parent UUID {parent_draft_uuid}")
        return draft_record

    @classmethod
    def get_with_family(
        cls,
        session: Session,
        draft_uuid: str
    ) -> tuple[Optional["DraftRecord"], Optional["DraftRecord"], list["DraftRecord"]]:
        """Get a draft record along with its parent and children.

        Args:
            session: SQLModel Session for database operations
            draft_uuid: The draft_id (UUID string) to look up

        Returns:
            Tuple of (draft, parent, children) where:
            - draft: The requested DraftRecord or None if not found
            - parent: The parent DraftRecord or None if no parent
            - children: List of child DraftRecords (empty list if none)
        """
        from sqlmodel import select

        # Get the requested draft
        statement = select(DraftRecord).where(DraftRecord.draft_id == draft_uuid)
        draft = session.exec(statement).first()

        if not draft:
            return (None, None, [])

        # Get parent by UUID if it exists
        parent = None
        if draft.parent_draft_id:
            parent_stmt = select(DraftRecord).where(DraftRecord.draft_id == draft.parent_draft_id)
            parent = session.exec(parent_stmt).first()

        # Get children by looking for records that reference this draft's UUID
        children_stmt = select(DraftRecord).where(DraftRecord.parent_draft_id == draft.draft_id)
        children = list(session.exec(children_stmt).all())

        return (draft, parent, children)


class SQLDraftRecorder(ScribeAPIListener):
    """
    DraftRecorder that uses SQLModel to persist drafts in a SQLite database.

    Audio files and JSON files are still saved to disk, but draft and event
    metadata is stored in a shared database for easier querying.
    """

    def __init__(self, output_dir: Path, chunk_ring_seconds=5, enable_file_storage: bool = False):
        super().__init__(split_audio=False)
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._enable_file_storage = enable_file_storage

        # Set up database
        db_path = self._output_dir / "drafts.db"
        self._db_url = f"sqlite:///{db_path}"
        self._engine = create_engine(self._db_url, echo=False)

        # Create tables
        SQLModel.metadata.create_all(self._engine)

        # Recording state
        self._current_dir = None
        self._current_draft = None
        self._chunk_ring = AudioRingBuffer(max_seconds=chunk_ring_seconds)
        self._wav_file = None

    async def on_pipeline_ready(self, pipeline):
        pass

    async def on_pipeline_shutdown(self):
        await self._close()

    async def on_audio_event(self, event: AudioEvent):
        if not self._current_draft:
            if isinstance(event, AudioChunkEvent):
                self._chunk_ring.add(event)
            return

        if not isinstance(event, AudioChunkEvent):
            return

        # Only process audio chunks if file storage is enabled
        if not self._enable_file_storage:
            return

        async def write_from_event(event):
            data_to_write = np.concatenate(event.data)
            logger.debug("Saving  %d samples to wav file", len(data_to_write))
            self._wav_file.write(data_to_write)

        if self._wav_file is None:
            await self._open_wav_file(event)
            if self._chunk_ring.has_data():
                for event in self._chunk_ring.get_from(self._current_draft.timestamp-3):
                    await write_from_event(event)
                self._chunk_ring.clear()
        await write_from_event(event)

    async def _open_wav_file(self, event: AudioChunkEvent):
        if isinstance(event.channels, tuple):
            channels = event.channels[1]
        else:
            channels = event.channels
        samplerate = int(int(event.sample_rate))
        self._wav_file = sf.SoundFile(
            self._current_dir / "draft.wav",
            mode='w',
            samplerate=samplerate,
            channels=channels,
            subtype='PCM_16'
        )
        leading_seconds = 0.4
        leading_frames = int(samplerate * leading_seconds)
        silence_block = np.zeros((leading_frames, channels), dtype=np.float32)
        self._wav_file.write(silence_block)

    async def on_draft_event(self, event: DraftEvent):
        if isinstance(event, DraftStartEvent) or isinstance(event, DraftEndEvent):
            await self._close()
        if isinstance(event, DraftStartEvent):
            self._current_draft = event.draft
            # Only create directory if file storage is enabled
            if self._enable_file_storage:
                timestamp = datetime.fromtimestamp(self._current_draft.timestamp)
                timestr = timestamp.strftime("%Y-%m0%d_%H-%M-%S-%f")
                directory = self._output_dir / f"draft-{timestr}"
                directory.mkdir()
                self._current_dir = directory
        if isinstance(event, DraftRescanEvent):
            await self.add_draft(event.draft)

    async def add_draft(self, draft: Draft):
        """Add a draft to the database with validation.

        Args:
            draft: Draft instance to save

        Raises:
            ValueError: If draft_id already exists or parent_draft_id is invalid
        """
        with Session(self._engine) as session:
            from sqlmodel import select

            # Check if draft_id already exists
            existing = session.exec(
                select(DraftRecord).where(DraftRecord.draft_id == str(draft.draft_id))
            ).first()
            if existing:
                raise ValueError(
                    f"Draft with draft_id '{draft.draft_id}' already exists in database (record id: {existing.id})"
                )

            # Validate parent_draft_id if present
            if draft.parent_draft_id:
                parent_record = session.exec(
                    select(DraftRecord).where(DraftRecord.draft_id == draft.parent_draft_id)
                ).first()
                if not parent_record:
                    raise ValueError(
                        f"Parent draft with draft_id '{draft.parent_draft_id}' not found in database"
                    )

            # Create draft record
            draft_record = DraftRecord(
                draft_id=str(draft.draft_id),
                timestamp=draft.timestamp,
                start_text=draft.start_text,
                end_text=draft.end_text,
                full_text=draft.full_text,
                classname=str(draft.__class__),
                directory_path=str(self._current_dir) if self._current_dir else "",
                parent_draft_id=draft.parent_draft_id
            )
            session.add(draft_record)
            session.commit()
            logger.info(f"Saved draft {draft_record.id} to database")
        
    async def on_text_event(self, event: TextEvent):
        pass

    async def _close(self):
        # Close WAV file if it exists
        if self._wav_file:
            self._wav_file.close()
            self._wav_file = None

        # Save draft data
        if self._current_draft:
            # Save files only if file storage is enabled and directory exists
            if self._enable_file_storage and self._current_dir:
                # Save text file (for easy reading)
                text_path = self._current_dir / "first_draft.txt"
                with open(text_path, 'w') as f:
                    f.write(self._current_draft.full_text)

                # Save JSON file (for compatibility)
                json_draft_path = self._current_dir / "first_draft.json"
                json_draft = {
                    'classname': str(self._current_draft.__class__),
                    'properties': asdict(self._current_draft)
                }
                with open(json_draft_path, 'w') as f:
                    json.dump(json_draft, f, indent=2)

            # Save to database
            await self._save_to_database()

            self._current_draft = None

        self._current_dir = None

    async def _save_to_database(self):
        """Save draft to SQLite database"""
        with Session(self._engine) as session:
            # Create draft record
            # directory_path will be empty string if file storage disabled
            draft_record = DraftRecord(
                draft_id=str(self._current_draft.draft_id),
                timestamp=self._current_draft.timestamp,
                start_text=self._current_draft.start_text,
                end_text=self._current_draft.end_text,
                full_text=self._current_draft.full_text,
                classname=str(self._current_draft.__class__),
                directory_path=str(self._current_dir) if self._current_dir else ""
            )
            session.add(draft_record)
            session.commit()
            logger.info(f"Saved draft {draft_record.id} to database")

    def get_all_drafts(self) -> list[DraftRecord]:
        """Query all drafts from database"""
        with Session(self._engine) as session:
            from sqlmodel import select
            statement = select(DraftRecord)
            return list(session.exec(statement).all())

    def get_draft_by_id(self, draft_id: int) -> Optional[DraftRecord]:
        """Get a specific draft by database ID"""
        with Session(self._engine) as session:
            return session.get(DraftRecord, draft_id)

    def get_draft_by_uuid(self, draft_uuid: str) -> Optional[DraftRecord]:
        """Get a specific draft by its draft_id (UUID)"""
        with Session(self._engine) as session:
            from sqlmodel import select
            statement = select(DraftRecord).where(DraftRecord.draft_id == draft_uuid)
            return session.exec(statement).first()

    def get_drafts_since(
        self,
        since_timestamp: float,
        limit: int = 100,
        offset: int = 0,
        order: str = "desc"
    ) -> tuple[list[DraftRecord], int]:
        """Query drafts with timestamp >= since_timestamp.

        Args:
            since_timestamp: Unix timestamp to filter from
            limit: Maximum number of results
            offset: Number of results to skip
            order: "asc" or "desc" for timestamp ordering

        Returns:
            Tuple of (drafts, total_count)
        """
        with Session(self._engine) as session:
            from sqlmodel import select, func, desc, asc

            # Build base query with filter
            base_query = select(DraftRecord).where(
                DraftRecord.timestamp >= since_timestamp
            )

            # Get total count
            count_query = select(func.count()).select_from(base_query.subquery())
            total = session.exec(count_query).one()

            # Apply ordering
            order_func = desc if order == "desc" else asc
            query = base_query.order_by(order_func(DraftRecord.timestamp))

            # Apply pagination
            query = query.limit(limit).offset(offset)

            drafts = list(session.exec(query).all())
            return (drafts, total)

    def get_all_drafts_paginated(
        self,
        limit: int = 100,
        offset: int = 0,
        order: str = "desc"
    ) -> tuple[list[DraftRecord], int]:
        """Query all drafts with pagination.

        Args:
            limit: Maximum number of results
            offset: Number of results to skip
            order: "asc" or "desc" for timestamp ordering

        Returns:
            Tuple of (drafts, total_count)
        """
        with Session(self._engine) as session:
            from sqlmodel import select, func, desc, asc

            # Get total count
            count_query = select(func.count()).select_from(DraftRecord)
            total = session.exec(count_query).one()

            # Build query with ordering
            order_func = desc if order == "desc" else asc
            query = select(DraftRecord).order_by(order_func(DraftRecord.timestamp))

            # Apply pagination
            query = query.limit(limit).offset(offset)

            drafts = list(session.exec(query).all())
            return (drafts, total)

    def get_draft_with_family(self, draft_uuid: str) -> tuple[Optional[DraftRecord], Optional[DraftRecord], list[DraftRecord]]:
        """Get draft with parent and children.

        Args:
            draft_uuid: The draft_id (UUID string) to look up

        Returns:
            Tuple of (draft, parent, children)
        """
        with Session(self._engine) as session:
            return DraftRecord.get_with_family(session, draft_uuid)
