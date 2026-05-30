"""
TicketingAgent — queries Ticketmaster, SeatGeek, Eventbrite, and StubHub directly,
normalizes the results into EventEntry objects, deduplicates, and inserts into
event_entry_database.

This agent shares the same deduplication and insertion pipeline as ConcertAgent so
that entries from both sources are cross-referenced against each other in the DB.
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Callable

from agent.duplicate_finder import DuplicateFinder
from agent.web_batch_parser import EventEntry
from db.operations import get_existing_venue_coords, insert_event_entries
from db.supabase_client import get_supabase_client
from ticketing import normalizer
from ticketing.eventbrite import EventbriteClient
from ticketing.seatgeek import SeatGeekClient
from ticketing.stubhub import StubHubClient
from ticketing.ticketmaster import TicketmasterClient
from utils.geocoder import enrich_entries_with_coords
from utils.id_generator import IDGenerator
from utils.logger import get_logger

logger = get_logger(__name__)

DAYS_AHEAD = 90


class TicketingAgent:
    def __init__(self):
        self._supabase = get_supabase_client()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        run_start = time.time()
        entry_batch_id = datetime.now().strftime("%m%d%Y_%H%M%S") + "_ticketing"
        logger.info(f"=== Ticketing Run START | entry_batch_id={entry_batch_id} ===")

        stats = {
            "ticketmaster": 0,
            "seatgeek": 0,
            "eventbrite": 0,
            "stubhub": 0,
            "raw_entries": 0,
            "dupes_intrabatch": 0,
            "dupes_crossdb": 0,
            "entries_inserted": 0,
        }

        # ------------------------------------------------------------------
        # Step 1 — Fetch from all platforms concurrently
        # ------------------------------------------------------------------
        self._step_log("Step 1: Fetch from ticketing platforms")
        all_entries: list[EventEntry] = []

        sources: list[tuple[str, Callable[[], list[EventEntry]]]] = []
        if os.environ.get("TICKETMASTER_API_KEY"):
            sources.append(("ticketmaster", self._fetch_ticketmaster))
        else:
            logger.warning("TICKETMASTER_API_KEY not set — skipping Ticketmaster")
        if os.environ.get("SEATGEEK_CLIENT_ID"):
            sources.append(("seatgeek", self._fetch_seatgeek))
        else:
            logger.warning("SEATGEEK_CLIENT_ID not set — skipping SeatGeek")
        if os.environ.get("EVENTBRITE_API_KEY"):
            sources.append(("eventbrite", self._fetch_eventbrite))
        else:
            logger.warning("EVENTBRITE_API_KEY not set — skipping Eventbrite")
        if os.environ.get("STUBHUB_CLIENT_ID") and os.environ.get("STUBHUB_CLIENT_SECRET"):
            sources.append(("stubhub", self._fetch_stubhub))
        else:
            logger.warning("STUBHUB_CLIENT_ID/SECRET not set — skipping StubHub")

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(fn): name for name, fn in sources}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    entries = future.result()
                    stats[name] = len(entries)
                    all_entries.extend(entries)
                    logger.info(f"  {name}: {len(entries)} entries")
                except Exception as e:
                    logger.error(f"  {name} fetch failed: {e}")

        stats["raw_entries"] = len(all_entries)
        logger.info(f"Total raw entries across all platforms: {len(all_entries)}")

        if not all_entries:
            logger.warning("No entries fetched from any platform — aborting run")
            return

        # ------------------------------------------------------------------
        # Step 2 — Assign IDs and batch ID
        # ------------------------------------------------------------------
        self._step_log("Step 2: Assign IDs")
        id_generator = IDGenerator(self._supabase)
        for entry in all_entries:
            entry.entry_batch_id = entry_batch_id
            entry.event_entry_id = id_generator.next()

        # ------------------------------------------------------------------
        # Step 3 — Intra-batch deduplication
        # ------------------------------------------------------------------
        self._step_log("Step 3: Intra-batch deduplication")
        dup_finder = DuplicateFinder(id_generator)
        try:
            pre = len(all_entries)
            all_entries = dup_finder.deduplicate_batch(all_entries)
            stats["dupes_intrabatch"] = pre - len(all_entries)
        except Exception as e:
            logger.error(f"Step 3 failed: {e}")

        # ------------------------------------------------------------------
        # Step 4 — Cross-reference against event_entry_database
        # ------------------------------------------------------------------
        self._step_log("Step 4: Cross-DB deduplication")
        try:
            pre = len(all_entries)
            all_entries = dup_finder.cross_reference_db(all_entries)
            stats["dupes_crossdb"] = pre - len(all_entries)
        except Exception as e:
            logger.error(f"Step 4 failed: {e}")

        # ------------------------------------------------------------------
        # Step 5 — Geocode venues
        # ------------------------------------------------------------------
        self._step_log("Step 5: Geocoding")
        try:
            known_coords = get_existing_venue_coords()
            entry_dicts = [e.model_dump() for e in all_entries]
            entry_dicts = enrich_entries_with_coords(entry_dicts, known_coords)
            for entry, d in zip(all_entries, entry_dicts):
                entry.address = d.get("address") or entry.address
                entry.lat = d.get("lat")
                entry.lng = d.get("lng")
        except Exception as e:
            logger.error(f"Step 5 failed: {e}")

        # ------------------------------------------------------------------
        # Step 6 — Insert into event_entry_database
        # ------------------------------------------------------------------
        self._step_log("Step 6: Insert entries")
        try:
            rows = [e.model_dump() for e in all_entries]
            stats["entries_inserted"] = insert_event_entries(rows)
        except Exception as e:
            logger.error(f"Step 6 failed: {e}")

        # ------------------------------------------------------------------
        # Summary
        # ------------------------------------------------------------------
        duration = time.time() - run_start
        logger.info(
            f"=== Ticketing Run COMPLETE | entry_batch_id={entry_batch_id} | "
            f"duration={duration:.1f}s ===\n"
            f"  Ticketmaster raw:       {stats['ticketmaster']}\n"
            f"  SeatGeek raw:           {stats['seatgeek']}\n"
            f"  Eventbrite raw:         {stats['eventbrite']}\n"
            f"  StubHub raw:            {stats['stubhub']}\n"
            f"  Total raw entries:      {stats['raw_entries']}\n"
            f"  Intra-batch dupes:      {stats['dupes_intrabatch']}\n"
            f"  Cross-DB dupes:         {stats['dupes_crossdb']}\n"
            f"  New entries inserted:   {stats['entries_inserted']}"
        )

    # ------------------------------------------------------------------
    # Per-platform fetch + normalize
    # ------------------------------------------------------------------

    def _fetch_ticketmaster(self) -> list[EventEntry]:
        with TicketmasterClient() as client:
            raw = client.fetch_nyc_concerts(days_ahead=DAYS_AHEAD)
        return [e for e in (normalizer.from_ticketmaster(r) for r in raw) if e is not None]

    def _fetch_seatgeek(self) -> list[EventEntry]:
        with SeatGeekClient() as client:
            raw = client.fetch_nyc_concerts(days_ahead=DAYS_AHEAD)
        return [e for e in (normalizer.from_seatgeek(r) for r in raw) if e is not None]

    def _fetch_eventbrite(self) -> list[EventEntry]:
        with EventbriteClient() as client:
            raw = client.fetch_nyc_concerts(days_ahead=DAYS_AHEAD)
        return [e for e in (normalizer.from_eventbrite(r) for r in raw) if e is not None]

    def _fetch_stubhub(self) -> list[EventEntry]:
        with StubHubClient() as client:
            raw = client.fetch_nyc_concerts(days_ahead=DAYS_AHEAD)
        return [e for e in (normalizer.from_stubhub(r) for r in raw) if e is not None]

    @staticmethod
    def _step_log(step_name: str) -> None:
        logger.info(f"--- {step_name} ---")
