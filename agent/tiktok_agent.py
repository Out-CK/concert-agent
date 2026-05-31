"""
TikTok Concert Agent — discovers upcoming NYC concerts via Nimble's TikTok agents
and inserts them into the Supabase event_entry_database.

Pipeline:
  0. Scrape curated NYC venue/promoter TikTok accounts directly.
  1. Search a curated list of concert-related hashtags concurrently.
  2. Merge and deduplicate by post_id, then keyword-filter.
  3. For high-signal videos, fetch the full video page (description + comments).
  4. Parse all content with Claude → EventEntry objects.
  5. Assign IDs, deduplicate intra-batch and cross-DB.
  6. Insert into event_entry_database.
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime

from agent.duplicate_finder import DuplicateFinder
from agent.tiktok_parser import TikTokParser
from agent.tiktok_venue_scraper import TikTokVenueScraper
from agent.web_batch_parser import EventEntry
from db.operations import insert_event_entries, get_existing_venue_coords
from db.supabase_client import get_supabase_client
from tools.nimble_tiktok_tool import NimbleTikTokHashtagTool, NimbleTikTokVideoTool
from utils.geocoder import enrich_entries_with_coords
from utils.id_generator import IDGenerator
from utils.logger import get_logger

logger = get_logger(__name__)

CONCURRENCY_LIMIT = 4

# Hashtags to search — ordered from most specific to broadest
HASHTAGS = [
    "nycconcert",
    "nycconerts",     # common alternate spelling that has real content
    "nycshows",
    "nyclivemusic",
    "concertnyc",
    "nycmusic",
    "nycevents",
    "livemusic",
]

# Caption must contain at least one of these to be worth parsing
_CONCERT_KEYWORDS_RE = re.compile(
    r"\b(concert|show|live|ticket|presale|venue|tonight|setlist|"
    r"opening act|sold out|general admission|ga |vip |"
    r"madison square|barclays|irving plaza|terminal 5|mercury lounge|"
    r"bowery|brooklyn steel|brooklyn mirage|pier\s*17|msg\b|"
    r"radio city|apollo|rough trade|knitting factory|"
    r"baby's all right|elsewhere|kings theatre|"
    r"music hall of williamsburg|mhow)\b",
    re.IGNORECASE,
)

# If any of these appear it's very likely a concrete concert post
_HIGH_SIGNAL_RE = re.compile(
    r"\b(ticket|presale|axs|ticketmaster|stubhub|dice\.fm|seetickets|"
    r"eventbrite|opening night|show starts|doors open|general admission)\b",
    re.IGNORECASE,
)


class TikTokConcertAgent:
    def __init__(self):
        self._hashtag_tool = NimbleTikTokHashtagTool()
        self._video_tool = NimbleTikTokVideoTool()
        self._venue_scraper = TikTokVenueScraper()
        self._supabase = get_supabase_client()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        run_start = time.time()
        entry_batch_id = datetime.now().strftime("%m%d%Y_%H%M%S")
        logger.info(f"=== TikTok Concert Run START | entry_batch_id={entry_batch_id} ===")

        stats = {
            "venue_posts_collected": 0,
            "hashtags_searched": 0,
            "videos_collected": 0,
            "videos_filtered": 0,
            "video_pages_fetched": 0,
            "entries_parsed": 0,
            "dupes_intrabatch": 0,
            "dupes_crossdb": 0,
            "entries_inserted": 0,
        }

        # ------------------------------------------------------------------
        # Step 0 — Scrape curated venue/promoter accounts
        # ------------------------------------------------------------------
        self._step_log("Step 0: Venue Account Scraping")
        seen_post_ids: set[str] = set()
        all_videos: list[dict] = []
        try:
            venue_posts = self._venue_scraper.scrape()
            for post in venue_posts:
                pid = post.get("post_id") or ""
                if pid and pid not in seen_post_ids:
                    seen_post_ids.add(pid)
                    all_videos.append(post)
            stats["venue_posts_collected"] = len(all_videos)
            logger.info(f"Venue accounts yielded {len(all_videos)} unique posts")
        except Exception as e:
            logger.error(f"Step 0 failed: {e}")

        # ------------------------------------------------------------------
        # Step 1 — Search TikTok hashtags
        # ------------------------------------------------------------------
        self._step_log("Step 1: TikTok Hashtag Searches")
        try:
            raw_results = asyncio.run(self._search_hashtags_concurrent(HASHTAGS))
            stats["hashtags_searched"] = len(HASHTAGS)

            for video in raw_results:
                pid = video.get("post_id") or ""
                if pid and pid not in seen_post_ids:
                    seen_post_ids.add(pid)
                    all_videos.append(video)

            stats["videos_collected"] = len(all_videos)
            logger.info(
                f"After hashtag search: {len(all_videos)} unique TikTok videos total "
                f"({len(all_videos) - stats['venue_posts_collected']} from hashtags)"
            )
        except Exception as e:
            logger.error(f"Step 1 failed: {e}")

        # ------------------------------------------------------------------
        # Step 2 — Filter by keywords
        # ------------------------------------------------------------------
        self._step_log("Step 2: Keyword Filtering")
        filtered_videos = self._filter_concert_videos(all_videos)
        stats["videos_filtered"] = len(filtered_videos)
        logger.info(f"Keyword filter kept {len(filtered_videos)}/{len(all_videos)} videos")

        # ------------------------------------------------------------------
        # Step 3 — Fetch full video pages for high-signal videos
        # ------------------------------------------------------------------
        self._step_log("Step 3: Fetch Full Video Pages (high-signal)")
        high_signal = [v for v in filtered_videos if self._is_high_signal(v)]
        if len(high_signal) > 20:
            logger.info(f"Capping high-signal videos from {len(high_signal)} to 20")
            high_signal = high_signal[:20]
        logger.info(f"Fetching full video pages for {len(high_signal)} high-signal videos")
        enriched = asyncio.run(self._enrich_video_pages_concurrent(high_signal))

        # Merge enriched video pages back onto the filtered list
        enriched_by_id = {v.get("post_id"): v for v in enriched if v.get("post_id")}
        records: list[dict] = []
        for video in filtered_videos:
            pid = video.get("post_id")
            records.append(enriched_by_id.get(pid, video))

        stats["video_pages_fetched"] = len(enriched_by_id)

        # ------------------------------------------------------------------
        # Step 4 — Parse with Claude
        # ------------------------------------------------------------------
        self._step_log("Step 4: Parse TikTok Records → EventEntries")
        id_generator = IDGenerator(self._supabase)
        entry_batch: list[EventEntry] = []
        try:
            raw_entries = TikTokParser().parse(records)
            stats["entries_parsed"] = len(raw_entries)
            for entry in raw_entries:
                entry.entry_batch_id = entry_batch_id
                entry.event_entry_id = id_generator.next()
            entry_batch = raw_entries
            logger.info(f"Parsed {len(entry_batch)} raw entries from TikTok")
        except Exception as e:
            logger.error(f"Step 4 failed: {e}")

        # ------------------------------------------------------------------
        # Step 5 — Geocoding Enrichment
        # ------------------------------------------------------------------
        self._step_log("Step 5: Geocoding Enrichment")
        try:
            known_coords = get_existing_venue_coords()
            entry_dicts = [e.model_dump() for e in entry_batch]
            entry_dicts = enrich_entries_with_coords(entry_dicts, known_coords)
            for entry, d in zip(entry_batch, entry_dicts):
                entry.address = d.get("address")
                entry.lat = d.get("lat")
                entry.lng = d.get("lng")
        except Exception as e:
            logger.error(f"Step 5 failed: {e}")

        # ------------------------------------------------------------------
        # Step 6 — Intra-Batch Deduplication
        # ------------------------------------------------------------------
        self._step_log("Step 6: Intra-Batch Deduplication")
        dup_finder = DuplicateFinder(id_generator)
        try:
            pre_count = len(entry_batch)
            entry_batch = dup_finder.deduplicate_batch(entry_batch)
            stats["dupes_intrabatch"] = pre_count - len(entry_batch)
        except Exception as e:
            logger.error(f"Step 6 failed: {e}")

        # ------------------------------------------------------------------
        # Step 7 — Cross-DB Deduplication
        # ------------------------------------------------------------------
        self._step_log("Step 7: Cross-DB Deduplication")
        try:
            pre_count = len(entry_batch)
            entry_batch = dup_finder.cross_reference_db(entry_batch)
            stats["dupes_crossdb"] = pre_count - len(entry_batch)
        except Exception as e:
            logger.error(f"Step 7 failed: {e}")

        # ------------------------------------------------------------------
        # Step 8 — Insert into Supabase
        # ------------------------------------------------------------------
        self._step_log("Step 8: Insert Event Entries")
        try:
            rows = [e.model_dump() for e in entry_batch]
            stats["entries_inserted"] = insert_event_entries(rows)
        except Exception as e:
            logger.error(f"Step 8 failed: {e}")

        # ------------------------------------------------------------------
        # Summary
        # ------------------------------------------------------------------
        duration = time.time() - run_start
        logger.info(
            f"=== TikTok Concert Run COMPLETE | entry_batch_id={entry_batch_id} | "
            f"duration={duration:.1f}s ===\n"
            f"  Venue account posts:     {stats['venue_posts_collected']}\n"
            f"  Hashtags searched:       {stats['hashtags_searched']}\n"
            f"  Total unique videos:     {stats['videos_collected']}\n"
            f"  Videos after filtering:  {stats['videos_filtered']}\n"
            f"  Full pages fetched:      {stats['video_pages_fetched']}\n"
            f"  Raw entries parsed:      {stats['entries_parsed']}\n"
            f"  Intra-batch dupes:       {stats['dupes_intrabatch']}\n"
            f"  Cross-DB dupes:          {stats['dupes_crossdb']}\n"
            f"  New entries inserted:    {stats['entries_inserted']}"
        )

    # ------------------------------------------------------------------
    # Filtering helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _filter_concert_videos(videos: list[dict]) -> list[dict]:
        """Keep only videos whose caption matches concert-related keywords."""
        result = []
        for v in videos:
            caption = (v.get("caption") or "").strip()
            if _CONCERT_KEYWORDS_RE.search(caption):
                result.append(v)
        return result

    @staticmethod
    def _is_high_signal(video: dict) -> bool:
        """Return True if the caption strongly suggests a ticketed concert."""
        caption = (video.get("caption") or "").strip()
        return bool(_HIGH_SIGNAL_RE.search(caption))

    # ------------------------------------------------------------------
    # Concurrency helpers
    # ------------------------------------------------------------------

    async def _search_hashtags_concurrent(self, hashtags: list[str]) -> list[dict]:
        semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)

        async def search_one(tag: str):
            async with semaphore:
                loop = asyncio.get_event_loop()
                try:
                    videos = await loop.run_in_executor(
                        None, lambda: self._hashtag_tool._run(tag)
                    )
                    # Stamp which hashtag surfaced this video
                    for v in videos:
                        v.setdefault("source_tag", tag)
                    return videos
                except Exception as e:
                    logger.error(f"Hashtag search failed for #{tag}: {e}")
                    return []

        tasks = [search_one(tag) for tag in hashtags]
        results_nested = await asyncio.gather(*tasks)
        return [item for sublist in results_nested for item in sublist]

    async def _enrich_video_pages_concurrent(self, videos: list[dict]) -> list[dict]:
        semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)

        async def fetch_one(video: dict):
            async with semaphore:
                loop = asyncio.get_event_loop()
                try:
                    page = await loop.run_in_executor(
                        None,
                        lambda: self._video_tool._run(
                            video_id=video["post_id"],
                            account_id=video.get("creator_handle", ""),
                        ),
                    )
                    # Merge page details onto the original video dict
                    merged = {**video}
                    if page.get("description"):
                        merged["description"] = page["description"]
                    return merged
                except Exception as e:
                    logger.error(
                        f"Video page fetch failed for post_id={video.get('post_id')}: {e}"
                    )
                    return video

        tasks = [fetch_one(v) for v in videos]
        return list(await asyncio.gather(*tasks))

    @staticmethod
    def _step_log(step_name: str) -> None:
        logger.info(f"--- {step_name} ---")
