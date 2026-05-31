"""
Concert Agent — top-level orchestrator for the full Concert Run pipeline.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime

from agent.duplicate_finder import DuplicateFinder
from agent.link_finder import LinkFinderAgent
from agent.past_event_archiver import PastEventArchiver
from agent.search_plan import SearchPlanAgent
from agent.web_batch_parser import EventEntry, WebBatchParser
from db.operations import insert_event_entries, insert_web_batch, get_existing_venue_coords
from db.supabase_client import get_supabase_client
from tools.nimble_extract_tool import NimbleExtractTool
from tools.nimble_search_tool import NimbleSearchTool
from utils.geocoder import enrich_entries_with_coords
from utils.id_generator import IDGenerator
from utils.logger import get_logger

logger = get_logger(__name__)

CONCURRENCY_LIMIT = 5


class ConcertAgent:
    def __init__(self):
        self._search_tool = NimbleSearchTool()
        self._extract_tool = NimbleExtractTool()
        self._supabase = get_supabase_client()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        run_start = time.time()
        entry_batch_id = datetime.now().strftime("%m%d%Y_%H%M%S")
        web_batch_id = datetime.now().strftime("%m%d%Y")
        logger.info(f"=== Concert Run START | entry_batch_id={entry_batch_id} ===")

        stats = {
            "queries_executed": 0,
            "pages_round1": 0,
            "pages_round2": 0,
            "entries_parsed": 0,
            "dupes_intrabatch": 0,
            "dupes_crossdb": 0,
            "entries_inserted": 0,
            "entries_archived": 0,
        }

        # ------------------------------------------------------------------
        # Step 1 — Generate Search Plan
        # ------------------------------------------------------------------
        self._step_log("Step 1: Generate Search Plan")
        try:
            search_plan = SearchPlanAgent().generate()
        except Exception as e:
            logger.error(f"Step 1 failed: {e}")
            return

        # ------------------------------------------------------------------
        # Step 2 — Web Search Round 1
        # ------------------------------------------------------------------
        self._step_log("Step 2: Web Search Round 1")
        try:
            round1_results = asyncio.run(
                self._run_searches_concurrent(search_plan.queries)
            )
            stats["queries_executed"] = len(search_plan.queries)

            # Deduplicate by URL
            seen_urls: set[str] = set()
            web_batch: list[dict] = []
            for result in round1_results:
                if result["url"] not in seen_urls:
                    seen_urls.add(result["url"])
                    web_batch.append(result)

            stats["pages_round1"] = len(web_batch)
            logger.info(f"Round 1: {len(web_batch)} unique pages collected")
        except Exception as e:
            logger.error(f"Step 2 failed: {e}")
            web_batch = []

        # ------------------------------------------------------------------
        # Step 3 — Store Round 1 Web Batch
        # ------------------------------------------------------------------
        self._step_log("Step 3: Store Round 1 Web Batch")
        if web_batch:
            try:
                db_records = [
                    {
                        "web_batch_id": web_batch_id,
                        "source_url": r["url"],
                        "query_used": r.get("query_used", ""),
                        "round": 1,
                        "content": r.get("content", ""),
                    }
                    for r in web_batch
                ]
                insert_web_batch(db_records)
            except Exception as e:
                logger.error(f"Step 3 failed: {e}")

        # ------------------------------------------------------------------
        # Step 4 — Find Additional Concert Links
        # ------------------------------------------------------------------
        self._step_log("Step 4: Link Finder")
        additional_urls: list[str] = []
        try:
            additional_urls = LinkFinderAgent().find_links(web_batch, seen_urls)
            logger.info(f"Link Finder found {len(additional_urls)} additional URLs")
        except Exception as e:
            logger.error(f"Step 4 failed: {e}")

        # ------------------------------------------------------------------
        # Step 5 — Web Extract Round 2
        # ------------------------------------------------------------------
        self._step_log("Step 5: Web Extract Round 2")
        round2_batch: list[dict] = []
        try:
            if additional_urls:
                round2_results = asyncio.run(
                    self._run_extracts_concurrent(additional_urls)
                )
                round2_batch = [r for r in round2_results if r.get("content")]
                stats["pages_round2"] = len(round2_batch)
                logger.info(f"Round 2: {len(round2_batch)} pages extracted")
        except Exception as e:
            logger.error(f"Step 5 failed: {e}")

        # ------------------------------------------------------------------
        # Step 6 — Store Round 2 Web Content
        # ------------------------------------------------------------------
        self._step_log("Step 6: Store Round 2 Web Content")
        if round2_batch:
            try:
                db_records_r2 = [
                    {
                        "web_batch_id": web_batch_id,
                        "source_url": r["url"],
                        "query_used": "link_finder",
                        "round": 2,
                        "content": r.get("content", ""),
                    }
                    for r in round2_batch
                ]
                insert_web_batch(db_records_r2)
            except Exception as e:
                logger.error(f"Step 6 failed: {e}")

        # ------------------------------------------------------------------
        # Step 7 — Parse Web Batch into Event Entries
        # ------------------------------------------------------------------
        self._step_log("Step 7: Parse Web Batch")
        id_generator = IDGenerator(self._supabase)
        full_batch = web_batch + round2_batch
        entry_batch: list[EventEntry] = []
        try:
            raw_entries = WebBatchParser().parse(full_batch)
            stats["entries_parsed"] = len(raw_entries)
            # Assign IDs
            for entry in raw_entries:
                entry.entry_batch_id = entry_batch_id
                entry.event_entry_id = id_generator.next()
            entry_batch = raw_entries
            logger.info(f"Parsed {len(entry_batch)} raw entries")
        except Exception as e:
            logger.error(f"Step 7 failed: {e}")

        # ------------------------------------------------------------------
        # Step 7b — Enrich with Venue Addresses
        # ------------------------------------------------------------------
        self._step_log("Step 7b: Geocoding Enrichment")
        try:
            known_coords = get_existing_venue_coords()
            entry_dicts = [e.model_dump() for e in entry_batch]
            entry_dicts = enrich_entries_with_coords(entry_dicts, known_coords)
            # Write geocoding results back onto EventEntry objects
            for entry, d in zip(entry_batch, entry_dicts):
                entry.address = d.get("address")
                entry.lat = d.get("lat")
                entry.lng = d.get("lng")
        except Exception as e:
            logger.error(f"Step 7b failed: {e}")

        # ------------------------------------------------------------------
        # Step 7c — Media Enrichment
        # ------------------------------------------------------------------
        self._step_log("Step 7c: Media Enrichment")
        try:
            from agent.media_enricher import MediaEnricher
            entry_batch = MediaEnricher().enrich(entry_batch)
        except Exception as e:
            logger.error(f"Step 7c failed: {e}")

        # ------------------------------------------------------------------
        # Step 8 — Intra-Batch Deduplication
        # ------------------------------------------------------------------
        self._step_log("Step 8: Intra-Batch Deduplication")
        dup_finder = DuplicateFinder(id_generator)
        try:
            pre_count = len(entry_batch)
            entry_batch = dup_finder.deduplicate_batch(entry_batch)
            stats["dupes_intrabatch"] = pre_count - len(entry_batch)
        except Exception as e:
            logger.error(f"Step 8 failed: {e}")

        # ------------------------------------------------------------------
        # Step 9 — Cross-Reference Against Event Entry Database
        # ------------------------------------------------------------------
        self._step_log("Step 9: Cross-DB Deduplication")
        try:
            pre_count = len(entry_batch)
            entry_batch = dup_finder.cross_reference_db(entry_batch)
            stats["dupes_crossdb"] = pre_count - len(entry_batch)
        except Exception as e:
            logger.error(f"Step 9 failed: {e}")

        # ------------------------------------------------------------------
        # Step 10 — Insert Entry Batch into Event Entry Database
        # ------------------------------------------------------------------
        self._step_log("Step 10: Insert Event Entries")
        try:
            # Re-generate IDs immediately before insert to avoid conflicts with
            # concurrent runs that may have inserted entries since Step 7 initialized
            # the IDGenerator (geocoding + dedup can take a long time).
            fresh_id_gen = IDGenerator(self._supabase)
            for entry in entry_batch:
                entry.event_entry_id = fresh_id_gen.next()
            rows = [e.model_dump() for e in entry_batch]
            stats["entries_inserted"] = insert_event_entries(rows)
        except Exception as e:
            logger.error(f"Step 10 failed: {e}")

        # ------------------------------------------------------------------
        # Step 11 — Archive Past Events
        # ------------------------------------------------------------------
        self._step_log("Step 11: Archive Past Events")
        try:
            stats["entries_archived"] = PastEventArchiver().run()
        except Exception as e:
            logger.error(f"Step 11 failed: {e}")

        # ------------------------------------------------------------------
        # Step 12 — Summary
        # ------------------------------------------------------------------
        duration = time.time() - run_start
        logger.info(
            f"=== Concert Run COMPLETE | entry_batch_id={entry_batch_id} | "
            f"duration={duration:.1f}s ===\n"
            f"  Queries executed:        {stats['queries_executed']}\n"
            f"  Pages fetched (Round 1): {stats['pages_round1']}\n"
            f"  Pages fetched (Round 2): {stats['pages_round2']}\n"
            f"  Raw entries parsed:      {stats['entries_parsed']}\n"
            f"  Intra-batch dupes removed: {stats['dupes_intrabatch']}\n"
            f"  Cross-DB dupes removed:    {stats['dupes_crossdb']}\n"
            f"  New entries inserted:    {stats['entries_inserted']}\n"
            f"  Entries archived:        {stats['entries_archived']}"
        )

    # ------------------------------------------------------------------
    # Concurrency helpers
    # ------------------------------------------------------------------

    async def _run_searches_concurrent(self, queries) -> list[dict]:
        semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)

        async def search_one(sq):
            async with semaphore:
                loop = asyncio.get_event_loop()
                try:
                    results = await loop.run_in_executor(
                        None, lambda: self._search_tool._run(sq.query, sq.query_type)
                    )
                    return [
                        {**r, "query_used": sq.query}
                        for r in results
                    ]
                except Exception as e:
                    logger.error(f"Search failed for '{sq.query}': {e}")
                    return []

        tasks = [search_one(sq) for sq in queries]
        results_nested = await asyncio.gather(*tasks)
        return [item for sublist in results_nested for item in sublist]

    async def _run_extracts_concurrent(self, urls: list[str]) -> list[dict]:
        semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)

        async def extract_one(url: str):
            async with semaphore:
                loop = asyncio.get_event_loop()
                try:
                    return await loop.run_in_executor(
                        None, lambda: self._extract_tool._run(url)
                    )
                except Exception as e:
                    logger.error(f"Extract failed for '{url}': {e}")
                    return {"url": url, "content": None}

        tasks = [extract_one(u) for u in urls]
        return list(await asyncio.gather(*tasks))

    @staticmethod
    def _step_log(step_name: str) -> None:
        logger.info(f"--- {step_name} ---")
