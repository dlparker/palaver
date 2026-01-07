    # Add others like asyncio for event 
import logging
from typing import Optional
import httpx
from palaver_shared.serializers import draft_from_draft_record_dict

logger = logging.getLogger("PalaverRestClient")


class PalaverRestClient:
    """Client for palaver's REST API to fetch drafts."""

    def __init__(self, base_url):
        """
        Initialize palaver REST client.

        Args:
            base_url: Base URL of palaver server (default: http://localhost:8000)
        """
        self.base_url = base_url.rstrip('/')
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        """Async context manager entry."""
        self._client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self._client:
            await self._client.aclose()
            self._client = None

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
            raise RuntimeError("Client not initialized. Use 'async with' context manager.")

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
            raise RuntimeError("Client not initialized. Use 'async with' context manager.")

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
            raise RuntimeError("Client not initialized. Use 'async with' context manager.")

        url = f"{self.base_url}/drafts/{draft_id}"
        params = {
            "include_parent": include_parent,
            "include_children": include_children,
        }

        logger.info(f"Fetching draft {draft_id}")
        response = await self._client.get(url, params=params)
        response.raise_for_status()

        return response.json()

