"""
One-time script: geocode all existing event entries and backfill address, lat, lng.

Usage:
    python scripts/backfill_addresses.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from db.supabase_client import get_supabase_client
from utils.geocoder import lookup_coords
from utils.logger import setup_root_logger, get_logger

setup_root_logger()
logger = get_logger(__name__)


def backfill():
    client = get_supabase_client()

    # Fetch all entries missing lat/lng
    result = client.table("event_entry_database").select("event_entry_id, venue, address, lat, lng").execute()
    entries = result.data or []

    missing = [e for e in entries if e.get("lat") is None and e.get("venue") and e["venue"] != "<UNKNOWN>"]
    logger.info(f"Total entries: {len(entries)} | Missing coords: {len(missing)}")

    # Build unique venue → (lat, lng, address) cache
    venue_cache: dict[str, tuple | None] = {}
    unique_venues = list({e["venue"] for e in missing})
    logger.info(f"Unique venues to geocode: {len(unique_venues)}")

    for i, venue in enumerate(unique_venues):
        result_coords = lookup_coords(venue)
        venue_cache[venue] = result_coords
        if result_coords:
            lat, lng, address = result_coords
            logger.info(f"[{i+1}/{len(unique_venues)}] '{venue}' → ({lat:.4f}, {lng:.4f}) '{address}'")
        else:
            logger.warning(f"[{i+1}/{len(unique_venues)}] '{venue}' → not found")

    # Update entries in DB
    updated = 0
    failed = 0
    skipped = 0
    for entry in missing:
        coords = venue_cache.get(entry["venue"])
        if not coords:
            skipped += 1
            continue
        lat, lng, address = coords
        update: dict = {"lat": lat, "lng": lng}
        if not entry.get("address") and address:
            update["address"] = address
        try:
            client.table("event_entry_database").update(update).eq(
                "event_entry_id", entry["event_entry_id"]
            ).execute()
            updated += 1
        except Exception as e:
            logger.error(f"Failed to update {entry['event_entry_id']}: {e}")
            failed += 1

    logger.info(f"Backfill complete: {updated} updated, {skipped} skipped (no coords), {failed} failed")


if __name__ == "__main__":
    backfill()
