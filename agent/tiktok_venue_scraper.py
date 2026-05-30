"""
TikTokVenueScraper — scrapes a curated list of NYC venue and promoter TikTok accounts
and converts their recent posts into the same record format used by the hashtag pipeline
so they feed directly into TikTokParser.
"""
from __future__ import annotations

import asyncio
from typing import Any

from tools.nimble_tiktok_tool import NimbleTikTokAccountTool
from utils.logger import get_logger

logger = get_logger(__name__)

CONCURRENCY_LIMIT = 4

# Curated NYC venue and promoter TikTok handles
NYC_TIKTOK_ACCOUNTS = [
    # Venues
    "brooklynsteel",
    "bowerypresents",
    "irvingplaza",
    "mercuryloungenyc",
    "terminal5nyc",
    "radiocitymusichall",
    "barclayslive",
    "roughtradesnyc",
    "babysallrightbk",
    "elsewherebrooklyn",
    "kingstheatre",
    "mhownyc",
    "publicrecordsnyc",
    "bellhouseny",
    "pioneerworks",
    "boweryballroom",
    "thewarefield",       # Live Nation venue
    "summerstagenyc",
    "pier17nyc",
    "brooklynmirage",
    "avant_gardner",      # Brooklyn Mirage parent
    # Promoters / ticketing / aggregators
    "livenationnyc",
    "aegpresents",
    "dice.fm",
    "songkick",
    "bandsintown",
    "nycgo",              # NYC tourism / events
    "timeout.newyork",
    # Music media / bloggers
    "nymag",
    "thenewyorker",
    "villagevoice",
    "brooklynvegan",
]


class TikTokVenueScraper:
    def __init__(self):
        self._account_tool = NimbleTikTokAccountTool()

    def scrape(self) -> list[dict[str, Any]]:
        """
        Scrape all curated venue accounts concurrently.
        Returns a flat list of video records in the same format as the hashtag feed
        (keys: post_id, caption, creator_handle, posted_at, description, source_tag).
        """
        logger.info(
            f"TikTokVenueScraper: scraping {len(NYC_TIKTOK_ACCOUNTS)} accounts"
        )
        results = asyncio.run(self._scrape_all_concurrent())
        logger.info(f"TikTokVenueScraper: collected {len(results)} posts total")
        return results

    async def _scrape_all_concurrent(self) -> list[dict[str, Any]]:
        semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)

        async def scrape_one(handle: str) -> list[dict[str, Any]]:
            async with semaphore:
                loop = asyncio.get_event_loop()
                try:
                    data = await loop.run_in_executor(
                        None, lambda: self._account_tool._run(handle)
                    )
                    return self._normalize_posts(handle, data)
                except Exception as e:
                    logger.error(f"TikTok account scrape failed for @{handle}: {e}")
                    return []

        tasks = [scrape_one(h) for h in NYC_TIKTOK_ACCOUNTS]
        results_nested = await asyncio.gather(*tasks)
        return [item for sublist in results_nested for item in sublist]

    @staticmethod
    def _normalize_posts(handle: str, data: dict[str, Any]) -> list[dict[str, Any]]:
        """Convert tiktok_account top_posts_data into the hashtag-feed record format."""
        posts = data.get("top_posts_data") or []
        records = []
        for post in posts:
            post_id = post.get("post_id") or ""
            description = post.get("description") or ""
            post_url = post.get("post_url") or (
                f"https://www.tiktok.com/@{handle}/video/{post_id}" if post_id else ""
            )
            records.append({
                "post_id": post_id,
                "caption": description,
                "description": description,
                "creator_handle": handle,
                "posted_at": post.get("create_date") or "",
                "source_tag": f"venue_account:{handle}",
                "tiktok_url": post_url,
            })
        return records
