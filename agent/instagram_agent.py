"""
InstagramAgent — discovers upcoming NYC concerts via Nimble's Instagram agents
and inserts them into the Supabase event_entry_database.

Pipeline:
  1. Scrape curated NYC venue/promoter Instagram profiles via instagram_profile_by_account.
  2. Store raw post content in event_web_database.
  3. Parse post captions into EventEntry objects with Claude.
  4. Enrich entries with venue addresses.
  5. Intra-batch deduplication.
  6. Cross-DB deduplication.
  7. Insert new entries into event_entry_database.
  8. Archive past events.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime

from agent.duplicate_finder import DuplicateFinder
from agent.instagram_post_parser import InstagramPostParser
from agent.past_event_archiver import PastEventArchiver
from agent.web_batch_parser import EventEntry
from db.operations import (
    get_existing_venue_addresses,
    insert_event_entries,
    insert_web_batch,
)
from db.supabase_client import get_supabase_client
from tools.nimble_instagram_tool import NimbleInstagramProfileTool
from utils.geocoder import enrich_entries_with_coords
from utils.id_generator import IDGenerator
from utils.logger import get_logger

logger = get_logger(__name__)

CONCURRENCY_LIMIT = 4

# Curated NYC venue and promoter Instagram handles
NYC_INSTAGRAM_ACCOUNTS = [
    # Major venues
    "brooklynsteel",
    "bowerypresents",
    "irvingplaza",
    "mercuryloungenyc",
    "terminal5nyc",
    "radiocity",
    "thegardennyc",       # Madison Square Garden
    "barclayslive",
    "roughtradesnyc",
    "babysallrightbk",
    "elsewherebrooklyn",
    "kingstheatre",
    "musichallofw",       # Music Hall of Williamsburg
    "publicrecordsnyc",
    "thebellhouseny",
    "pionerrworks",
    "boweryballroom",
    "summerstage",
    "pier17nyc",
    "brooklynmirage",
    "avant_gardner",
    "knittingfactory",
    "rockwoodmusichall",
    "unionpoolbar",
    "pianos_nyc",
    "nublu151",
    "theslantedroom",     # smaller venue
    "zebulon.nyc",
    "shea_stadium_bk",
    "thestudioatwebster",
    "websterhall",
    "houseofyes",
    "clockworknyc",
    "thetribecaball",
    "jalopynyc",
    "nublu151",
    # Promoters / ticketing
    "livenation",
    "aegpresents",
    "boweryelectric",
    "bowerypresents",
    "dice.fm",
    # Media / aggregators
    "brooklynvegan",
    "nycgo",
    "timeout.newyork",
    "papermag",
    "villagevoice",
    "songkick",
]

# Deduplicate the list in case of duplicates
NYC_INSTAGRAM_ACCOUNTS = list(dict.fromkeys(NYC_INSTAGRAM_ACCOUNTS))


class InstagramAgent:
    def __init__(self):
        self._profile_tool = NimbleInstagramProfileTool()
        self._supabase = get_supabase_client()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        run_start = time.time()
        entry_batch_id = datetime.now().strftime("%m%d%Y_%H%M%S") + "_ig"
        web_batch_id = datetime.now().strftime("%m%d%Y") + "_ig"
        logger.info(f"=== Instagram Run START | entry_batch_id={entry_batch_id} ===")

        stats = {
            "accounts_attempted": len(NYC_INSTAGRAM_ACCOUNTS),
            "profiles_scraped": 0,
            "posts_collected": 0,
            "entries_parsed": 0,
            "dupes_intrabatch": 0,
            "dupes_crossdb": 0,
            "entries_inserted": 0,
            "entries_archived": 0,
        }

        # ------------------------------------------------------------------
        # Step 1 — Scrape Instagram profiles via Nimble agent
        # ------------------------------------------------------------------
        self._step_log("Step 1: Scrape Instagram Profiles")
        post_pages: list[dict] = []
        try:
            raw_profiles = asyncio.run(
                self._scrape_profiles_concurrent(NYC_INSTAGRAM_ACCOUNTS)
            )
            # raw_profiles: list of (handle, profile_data) tuples
            for handle, profile_data in raw_profiles:
                if not profile_data:
                    continue
                posts = profile_data.get("posts") or []
                bio = profile_data.get("biography") or ""
                profile_url = (
                    profile_data.get("profile_url")
                    or f"https://www.instagram.com/{handle}/"
                )
                if not posts:
                    logger.debug(f"@{handle}: no posts returned")
                    continue

                stats["profiles_scraped"] += 1

                # Build one "page" record per profile: combine bio + all post captions
                combined_text = f"BIOGRAPHY: {bio}\n\n"
                for post in posts:
                    caption = self._extract_post_caption(post)
                    if caption:
                        combined_text += f"---\nPOST: {caption}\n"

                post_pages.append({
                    "url": profile_url,
                    "handle": handle,
                    "content": combined_text[:30000],
                })
                stats["posts_collected"] += len(posts)

            logger.info(
                f"Scraped {stats['profiles_scraped']}/{len(NYC_INSTAGRAM_ACCOUNTS)} profiles, "
                f"{stats['posts_collected']} posts total"
            )
        except Exception as e:
            logger.error(f"Step 1 failed: {e}")

        if not post_pages:
            logger.warning("No Instagram content retrieved — aborting Instagram Run")
            return

        # ------------------------------------------------------------------
        # Step 2 — Store Raw Instagram Content
        # ------------------------------------------------------------------
        self._step_log("Step 2: Store Raw Content")
        try:
            db_records = [
                {
                    "web_batch_id": web_batch_id,
                    "source_url": p["url"],
                    "query_used": "instagram_profile_agent",
                    "round": 1,
                    "content": p["content"],
                }
                for p in post_pages
            ]
            insert_web_batch(db_records)
        except Exception as e:
            logger.error(f"Step 2 failed: {e}")

        # ------------------------------------------------------------------
        # Step 3 — Parse Posts into Event Entries
        # ------------------------------------------------------------------
        self._step_log("Step 3: Parse Instagram Posts")
        id_generator = IDGenerator(self._supabase)
        entry_batch: list[EventEntry] = []
        try:
            raw_entries = InstagramPostParser().parse(post_pages)
            stats["entries_parsed"] = len(raw_entries)
            for entry in raw_entries:
                entry.entry_batch_id = entry_batch_id
                entry.event_entry_id = id_generator.next()
            entry_batch = raw_entries
            logger.info(f"Parsed {len(entry_batch)} raw entries from Instagram")
        except Exception as e:
            logger.error(f"Step 3 failed: {e}")

        # ------------------------------------------------------------------
        # Step 4 — Geocoding Enrichment
        # ------------------------------------------------------------------
        self._step_log("Step 4: Geocoding Enrichment")
        try:
            from db.operations import get_existing_venue_coords
            known_coords = get_existing_venue_coords()
            entry_dicts = [e.model_dump() for e in entry_batch]
            entry_dicts = enrich_entries_with_coords(entry_dicts, known_coords)
            for entry, d in zip(entry_batch, entry_dicts):
                entry.address = d.get("address")
                entry.lat = d.get("lat")
                entry.lng = d.get("lng")
        except Exception as e:
            logger.error(f"Step 4 failed: {e}")

        # ------------------------------------------------------------------
        # Step 5 — Intra-Batch Deduplication
        # ------------------------------------------------------------------
        self._step_log("Step 5: Intra-Batch Deduplication")
        dup_finder = DuplicateFinder(id_generator)
        try:
            pre_count = len(entry_batch)
            entry_batch = dup_finder.deduplicate_batch(entry_batch)
            stats["dupes_intrabatch"] = pre_count - len(entry_batch)
        except Exception as e:
            logger.error(f"Step 5 failed: {e}")

        # ------------------------------------------------------------------
        # Step 6 — Cross-DB Deduplication
        # ------------------------------------------------------------------
        self._step_log("Step 6: Cross-DB Deduplication")
        try:
            pre_count = len(entry_batch)
            entry_batch = dup_finder.cross_reference_db(entry_batch)
            stats["dupes_crossdb"] = pre_count - len(entry_batch)
        except Exception as e:
            logger.error(f"Step 6 failed: {e}")

        # ------------------------------------------------------------------
        # Step 7 — Insert Event Entries
        # ------------------------------------------------------------------
        self._step_log("Step 7: Insert Event Entries")
        try:
            rows = [e.model_dump() for e in entry_batch]
            stats["entries_inserted"] = insert_event_entries(rows)
        except Exception as e:
            logger.error(f"Step 7 failed: {e}")

        # ------------------------------------------------------------------
        # Step 8 — Archive Past Events
        # ------------------------------------------------------------------
        self._step_log("Step 8: Archive Past Events")
        try:
            stats["entries_archived"] = PastEventArchiver().run()
        except Exception as e:
            logger.error(f"Step 8 failed: {e}")

        # ------------------------------------------------------------------
        # Summary
        # ------------------------------------------------------------------
        duration = time.time() - run_start
        logger.info(
            f"=== Instagram Run COMPLETE | entry_batch_id={entry_batch_id} | "
            f"duration={duration:.1f}s ===\n"
            f"  Accounts attempted:      {stats['accounts_attempted']}\n"
            f"  Profiles scraped:        {stats['profiles_scraped']}\n"
            f"  Posts collected:         {stats['posts_collected']}\n"
            f"  Raw entries parsed:      {stats['entries_parsed']}\n"
            f"  Intra-batch dupes:       {stats['dupes_intrabatch']}\n"
            f"  Cross-DB dupes:          {stats['dupes_crossdb']}\n"
            f"  New entries inserted:    {stats['entries_inserted']}\n"
            f"  Entries archived:        {stats['entries_archived']}"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _scrape_profiles_concurrent(
        self, handles: list[str]
    ) -> list[tuple[str, dict]]:
        semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)

        async def scrape_one(handle: str) -> tuple[str, dict]:
            async with semaphore:
                loop = asyncio.get_event_loop()
                try:
                    data = await loop.run_in_executor(
                        None, lambda: self._profile_tool._run(handle)
                    )
                    return (handle, data)
                except Exception as e:
                    logger.error(f"Instagram profile failed for @{handle}: {e}")
                    return (handle, {})

        tasks = [scrape_one(h) for h in handles]
        return list(await asyncio.gather(*tasks))

    @staticmethod
    def _extract_post_caption(post: dict) -> str:
        """Pull the caption text out of a post object regardless of schema shape."""
        for key in ("caption", "description", "text", "edge_media_to_caption"):
            val = post.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
            # Instagram graph sometimes nests captions
            if isinstance(val, dict):
                edges = val.get("edges") or []
                if edges:
                    node_text = (edges[0].get("node") or {}).get("text") or ""
                    if node_text:
                        return node_text.strip()
        return ""

    @staticmethod
    def _step_log(step_name: str) -> None:
        logger.info(f"--- {step_name} ---")
