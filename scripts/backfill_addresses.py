"""
One-time script: look up and backfill addresses for all existing event entries.

Usage:
    python scripts/backfill_addresses.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from db.supabase_client import get_supabase_client
from utils.geocoder import lookup_address
from utils.logger import setup_root_logger, get_logger

setup_root_logger()
logger = get_logger(__name__)


def backfill():
    client = get_supabase_client()

    # Fetch all entries missing an address
    result = client.table("event_entry_database").select("event_entry_id, venue, address").execute()
    entries = result.data or []

    missing = [e for e in entries if not e.get("address") and e.get("venue") and e["venue"] != "<UNKNOWN>"]
    logger.info(f"Total entries: {len(entries)} | Missing address: {len(missing)}")

    # Build unique venue→address map to avoid redundant lookups
    venue_cache: dict[str, str] = {}
    unique_venues = list({e["venue"] for e in missing})
    logger.info(f"Unique venues to geocode: {len(unique_venues)}")

    for i, venue in enumerate(unique_venues):
        address = lookup_address(venue)
        venue_cache[venue] = address or ""
        logger.info(f"[{i+1}/{len(unique_venues)}] '{venue}' → '{address}'")

    # Update entries in DB
    updated = 0
    failed = 0
    for entry in missing:
        address = venue_cache.get(entry["venue"])
        if not address:
            continue
        try:
            client.table("event_entry_database").update({"address": address}).eq(
                "event_entry_id", entry["event_entry_id"]
            ).execute()
            updated += 1
        except Exception as e:
            logger.error(f"Failed to update {entry['event_entry_id']}: {e}")
            failed += 1

    logger.info(f"Backfill complete: {updated} updated, {failed} failed")


if __name__ == "__main__":
    backfill()
