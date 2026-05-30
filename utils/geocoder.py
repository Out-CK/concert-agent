"""
Venue address lookup via Nominatim (OpenStreetMap).
Returns a full street address string for a given venue name.
Respects Nominatim's 1 req/sec rate limit.
"""
from __future__ import annotations

import time
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from utils.logger import get_logger

logger = get_logger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
HEADERS = {"User-Agent": "ConcertAgent/1.0 (internal tool; contact@out-ck.com)"}
RATE_LIMIT_S = 1.1  # Nominatim policy: max 1 req/sec

_last_request: float = 0.0

# City/state suffixes to strip before querying
import re
_SUFFIX_RE = re.compile(
    r",?\s*(new york(?: city)?|nyc|brooklyn|queens|bronx|manhattan|staten island)"
    r"(,?\s*(ny|new york))?\s*$",
    re.IGNORECASE,
)


def _clean_venue(venue: str) -> str:
    return _SUFFIX_RE.sub("", venue.strip()).strip().rstrip(",").strip()


def _rate_limit() -> None:
    global _last_request
    elapsed = time.time() - _last_request
    if elapsed < RATE_LIMIT_S:
        time.sleep(RATE_LIMIT_S - elapsed)
    _last_request = time.time()


@retry(
    retry=retry_if_exception_type(httpx.HTTPError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=10),
)
def _nominatim_search(query: str) -> list[dict]:
    _rate_limit()
    resp = httpx.get(
        NOMINATIM_URL,
        params={
            "q": query,
            "format": "json",
            "addressdetails": 1,
            "limit": 1,
            "countrycodes": "us",
        },
        headers=HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def _build_address(result: dict) -> str:
    """Convert a Nominatim result into a clean street address."""
    addr = result.get("address", {})
    parts = []

    # House number + road
    if addr.get("house_number") and addr.get("road"):
        parts.append(f"{addr['house_number']} {addr['road']}")
    elif addr.get("road"):
        parts.append(addr["road"])

    # Neighbourhood / suburb / city
    city = (
        addr.get("city")
        or addr.get("town")
        or addr.get("village")
        or addr.get("county")
        or ""
    )
    if city:
        parts.append(city)

    state = addr.get("state", "")
    postcode = addr.get("postcode", "")
    if state and postcode:
        parts.append(f"{state} {postcode}")
    elif state:
        parts.append(state)

    return ", ".join(parts) if parts else result.get("display_name", "")


_BOROUGH_RE = re.compile(
    r"\b(brooklyn|bronx|queens|staten island)\b", re.IGNORECASE
)


def lookup_coords(venue: str) -> Optional[tuple[float, float, str]]:
    """
    Return (lat, lng, address) for a venue name, or None if not found.
    Tries multiple strategies: borough-aware queries first, then NYC fallback,
    then bare venue name.
    """
    cleaned = _clean_venue(venue)

    # If the original venue name mentions a specific borough, try that first
    borough_match = _BOROUGH_RE.search(venue)
    queries: list[str] = []
    if borough_match:
        borough = borough_match.group(1).title()
        queries.append(f"{cleaned}, {borough}, NY")
    queries.append(f"{cleaned}, New York City, NY")
    if not borough_match:
        queries.append(f"{cleaned}, Manhattan, NY")
    queries.append(cleaned)

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_queries = [q for q in queries if not (q in seen or seen.add(q))]  # type: ignore[func-returns-value]

    for query in unique_queries:
        try:
            results = _nominatim_search(query)
            if results:
                r = results[0]
                lat = float(r["lat"])
                lng = float(r["lon"])
                address = _build_address(r)
                logger.debug(f"Geocoded '{venue}' via '{query}' → ({lat}, {lng}) '{address}'")
                return lat, lng, address
        except Exception as e:
            logger.warning(f"Nominatim lookup failed for '{venue}' (query='{query}'): {e}")
    logger.warning(f"No coords found for venue: '{venue}'")
    return None


# Keep old name as an alias for any callers that only need the address string
def lookup_address(venue: str) -> Optional[str]:
    result = lookup_coords(venue)
    return result[2] if result else None


def enrich_entries_with_coords(
    entries: list[dict],
    existing_cache: dict[str, tuple[float, float, str]] | None = None,
) -> list[dict]:
    """
    Add address, lat, and lng fields to each entry dict by geocoding its venue.
    existing_cache maps venue → (lat, lng, address) to avoid redundant lookups.
    """
    cache: dict[str, tuple[float, float, str] | None] = dict(existing_cache or {})
    unique_venues = {e["venue"] for e in entries if e.get("venue") and e.get("venue") != "<UNKNOWN>"}
    to_fetch = [v for v in unique_venues if v not in cache]

    logger.info(f"Geocoding: {len(unique_venues)} unique venues, {len(to_fetch)} need lookup")

    for venue in to_fetch:
        cache[venue] = lookup_coords(venue)

    for entry in entries:
        venue = entry.get("venue", "")
        result = cache.get(venue)
        if result:
            lat, lng, address = result
            if not entry.get("address"):
                entry["address"] = address
            entry["lat"] = lat
            entry["lng"] = lng
        else:
            entry.setdefault("lat", None)
            entry.setdefault("lng", None)

    resolved = sum(1 for v in to_fetch if cache.get(v))
    logger.info(f"Geocoding complete: {resolved}/{len(to_fetch)} resolved")
    return entries


# Keep old name as an alias
def enrich_entries_with_addresses(entries: list[dict], existing_cache: dict[str, str] | None = None) -> list[dict]:
    coord_cache: dict[str, tuple[float, float, str] | None] = {}
    if existing_cache:
        # Convert old address-only cache — we don't have coords for these, leave them to be fetched
        pass
    return enrich_entries_with_coords(entries, coord_cache)
