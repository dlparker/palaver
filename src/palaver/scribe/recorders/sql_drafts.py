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
from palaver.scribe.audio_events import AudioEvent, AudioChunkEvent, AudioRingBuffer
from palaver.scribe.api import ScribeAPIListener
from palaver.scribe.text_events import TextEvent
from palaver.scribe.draft_events import DraftEvent, DraftStartEvent, DraftEndEvent

logger = logging.getLogger("SQLDraftRecorder")


class DraftRecord(SQLModel, table=True):
    """SQLModel for persisting draft information"""
    __tablename__ = "drafts"

    id: Optional[int] = Field(default=None, primary_key=True)
    draft_id: str = Field(index=True)  # UUID for draft lookup
    timestamp: float  # Original draft timestamp
    full_text: str
    classname: str
    directory_path: str  # Path to the draft-{timestamp} directory
    created_at: datetime = Field(default_factory=datetime.now)
    properties_json: str  # JSON serialized draft properties

    # Relationship to events
    events: list["EventRecord"] = Relationship(back_populates="draft")


class EventRecord(SQLModel, table=True):
    """SQLModel for persisting events associated with a draft"""
    __tablename__ = "events"

    id: Optional[int] = Field(default=None, primary_key=True)
    draft_id: Optional[int] = Field(foreign_key="drafts.id")
    sequence: int  # Order of occurrence
    event_classname: str
    event_data_json: str  # JSON serialized event properties
    timestamp: datetime = Field(default_factory=datetime.now)

    # Relationship to draft
    draft: Optional[DraftRecord] = Relationship(back_populates="events")


class SQLDraftRecorder(ScribeAPIListener):
    """
    DraftRecorder that uses SQLModel to persist drafts in a SQLite database.

    Audio files and JSON files are still saved to disk, but draft and event
    metadata is stored in a shared database for easier querying.
    """

    def __init__(self, output_dir: Path, chunk_ring_seconds=5):
        super().__init__(split_audio=False)
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

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
        self._events = []
        self._event_sequence = 0

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
            self._events.append(event)
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
        self._events.append(event)
        if isinstance(event, DraftStartEvent) or isinstance(event, DraftEndEvent):
            await self._close()
        if isinstance(event, DraftStartEvent):
            self._current_draft = event.draft
            self._event_sequence = 0
            timestamp = datetime.fromtimestamp(self._current_draft.timestamp)
            timestr = timestamp.strftime("%Y-%m0%d_%H-%M-%S-%f")
            directory = self._output_dir / f"draft-{timestr}"
            directory.mkdir()
            self._current_dir = directory

    async def on_text_event(self, event: TextEvent):
        self._events.append(event)

    async def _close(self):
        if self._current_dir:
            # Close WAV file
            if self._wav_file:
                self._wav_file.close()
                self._wav_file = None

            # Save draft data
            if self._current_draft:
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
            self._event_sequence = 0

    async def _save_to_database(self):
        """Save draft and events to SQLite database"""
        with Session(self._engine) as session:
            # Create draft record
            draft_record = DraftRecord(
                draft_id=str(self._current_draft.draft_id),
                timestamp=self._current_draft.timestamp,
                full_text=self._current_draft.full_text,
                classname=str(self._current_draft.__class__),
                directory_path=str(self._current_dir),
                properties_json=json.dumps(asdict(self._current_draft))
            )
            session.add(draft_record)
            session.commit()
            session.refresh(draft_record)

            # Create event records
            if len(self._events) > 0:
                for idx, event in enumerate(self._events):
                    event_record = EventRecord(
                        draft_id=draft_record.id,
                        sequence=idx,
                        event_classname=str(event.__class__),
                        event_data_json=json.dumps(asdict(event))
                    )
                    session.add(event_record)

                session.commit()
                logger.info(f"Saved draft {draft_record.id} with {len(self._events)} events to database")

            self._events = []

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
