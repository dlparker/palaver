import asyncio
from fire import Fire
from palaver_client.rest_client import PalaverRestClient


class DraftFetcher:

    async def get_latest(self):
        async with PalaverRestClient() as client:
            drafts = await client.fetch_all_drafts(limit=1)
            if drafts:
                return drafts[0]
            return None

if __name__ == "__main__":
    Fire(DraftFetcher)

        
