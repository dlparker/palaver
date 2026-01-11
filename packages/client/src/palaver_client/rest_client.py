    # Add others like asyncio for event 
import logging
from typing import Optional
import httpx
from palaver_shared.serializers import draft_from_draft_record_dict

logger = logging.getLogger("PalaverRestClient")


class PalaverRestClient:
    """Client for palaver's REST API to fetch drafts.

    Can be used either with async context manager or manual connect/close:

    Context manager usage:
        async with PalaverRestClient(base_url) as client:
            drafts, total = await client.fetch_all_drafts()

    Manual usage:
        client = PalaverRestClient(base_url)
        await client.connect()
        try:
            drafts, total = await client.fetch_all_drafts()
        finally:
            await client.close()
    """

    def __init__(self, base_url):
        """
        Initialize palaver REST client.

        Args:
            base_url: Base URL of palaver server (default: http://localhost:8000)
        """
        self.base_url = base_url.rstrip('/')
        self._client: Optional[httpx.AsyncClient] = None

    async def connect(self):
        """
        Manually connect the client.

        Must call close() when done, or use the async context manager instead.
        Calling connect() multiple times is safe (idempotent).
        """
        if self._client is not None:
            logger.debug("Client already connected")
            return
        self._client = httpx.AsyncClient(timeout=30.0)
        logger.debug(f"Connected to {self.base_url}")

    async def close(self):
        """
        Manually close the client connection.

        Safe to call multiple times (idempotent).
        """
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.debug("Client closed")

    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

    async def fetch_drafts_since(self,
                                 since_timestamp: float,
                                 limit: int = 100,
                                 offset: int = 0,
                                 order: str = "desc"
                                 ) -> tuple[list[dict], int]:
        """
        Fetch drafts created after a specific timestamp.

        Args:
            since_timestamp: Unix timestamp to fetch drafts after
            limit: Maximum number of results (1-1000, default 100)
            offset: Number of results to skip (default 0)
            order: Sort order "asc" or "desc" (default "desc")

        Returns:
            Tuple of (list of draft dicts, total count)

        Raises:
            httpx.HTTPError: If request fails
        """
        if not self._client:
            raise RuntimeError("Client not connected. Call connect() or use 'async with' context manager.")

        url = f"{self.base_url}/drafts"
        params = {
            "since": str(since_timestamp),
            "limit": limit,
            "offset": offset,
            "order": order,
        }

        logger.info(f"Fetching drafts since {since_timestamp} from {url}")
        response = await self._client.get(url, params=params)
        response.raise_for_status()

        data = response.json()
        drafts = []
        for d_dict in data["drafts"]:
            drafts.append(draft_from_draft_record_dict(d_dict))
        total = data["total"]

        logger.info(f"Fetched {len(drafts)} drafts (total: {total})")
        return drafts, total

    async def fetch_all_drafts(self,
                               limit: int = 100,
                               offset: int = 0,
                               order: str = "desc") -> tuple[list[dict], int]:
        """
        Fetch all drafts with pagination.

        Args:
            limit: Maximum number of results (1-1000, default 100)
            offset: Number of results to skip (default 0)
            order: Sort order "asc" or "desc" (default "desc")

        Returns:
            Tuple of (list of draft dicts, total count)

        Raises:
            httpx.HTTPError: If request fails
        """
        if not self._client:
            raise RuntimeError("Client not connected. Call connect() or use 'async with' context manager.")

        url = f"{self.base_url}/drafts"
        params = {
            "limit": limit,
            "offset": offset,
            "order": order,
        }

        logger.info(f"Fetching all drafts from {url}")
        response = await self._client.get(url, params=params)
        response.raise_for_status()

        data = response.json()
        drafts = []
        for d_dict in data["drafts"]:
            drafts.append(draft_from_draft_record_dict(d_dict))
        total = data["total"]

        logger.info(f"Fetched {len(drafts)} drafts (total: {total})")
        return drafts, total

    async def fetch_draft_by_id(self,
                                draft_id: str,
                                include_parent: bool = False,
                                include_children: bool = False) -> dict:
        """
        Fetch a specific draft by UUID.

        Args:
            draft_id: UUID of the draft to fetch
            include_parent: Include parent draft in response (default False)
            include_children: Include child drafts in response (default False)

        Returns:
            Dictionary with 'draft' key and optional 'parent', 'children' keys

        Raises:
            httpx.HTTPError: If request fails or draft not found (404)
        """
        if not self._client:
            raise RuntimeError("Client not connected. Call connect() or use 'async with' context manager.")

        url = f"{self.base_url}/drafts/{draft_id}"
        params = {
            "include_parent": include_parent,
            "include_children": include_children,
        }

        logger.info(f"Fetching draft {draft_id}")
        response = await self._client.get(url, params=params)
        response.raise_for_status()

        return response.json()

    async def text_to_speech(self, text: str) -> dict:
        """
        Convert text to speech and play through server's speaker.

        Args:
            text: The text to synthesize and play

        Returns:
            Dictionary with 'success' and 'message' keys

        Raises:
            httpx.HTTPError: If request fails or pipeline not available (503)
        """
        if not self._client:
            raise RuntimeError("Client not connected. Call connect() or use 'async with' context manager.")

        url = f"{self.base_url}/tts"
        payload = {"text": text}

        logger.info(f"Sending TTS request: {text[:50]}...")
        response = await self._client.post(url, json=payload)
        response.raise_for_status()

        return response.json()

    async def play_signal_sound(self, name: str) -> dict:
        """
        Play one of the named signal sounds

        Args:
            name: The name of a known signal sound

        Returns:
            Dictionary with 'success' and 'message' keys

        Raises:
            httpx.HTTPError: If request fails or pipeline not available (503)
        """
        if not self._client:
            raise RuntimeError("Client not connected. Call connect() or use 'async with' context manager.")

        url = f"{self.base_url}/play_signal_sound"
        payload = {"name": name}

        logger.info(f"Sending signal sound request: {name}...")
        response = await self._client.post(url, json=payload)
        response.raise_for_status()

        return response.json()

