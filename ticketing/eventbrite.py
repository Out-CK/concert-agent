"""
Eventbrite API v3 client.

Docs: https://www.eventbrite.com/platform/api
Requires: EVENTBRITE_API_KEY env var (create a private token at eventbrite.com/platform/api-keys)
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from utils.logger import get_logger

logger = get_logger(__name__)

BASE_URL = "https://www.eventbriteapi.com/v3/events/search/"
MUSIC_CATEGORY_ID = "103"  # Eventbrite category ID for Music
PAGE_SIZE = 50  # Eventbrite max is 50 per page


class EventbriteClient:
    def __init__(self):
        self._api_key = os.environ["EVENTBRITE_API_KEY"]
        self._client = httpx.Client(
            timeout=30,
            headers={"Authorization": f"Bearer {self._api_key}"},
        )

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def fetch_nyc_concerts(self, days_ahead: int = 90) -> list[dict]:
        """Return upcoming NYC music events within the next `days_ahead` days."""
        now = datetime.now(tz=timezone.utc)
        start = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        end = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%dT%H:%M:%SZ")

        all_events: list[dict] = []
        page = 1
        while True:
            data = self._fetch_page(page, start, end)
            events = data.get("events", [])
            all_events.extend(events)

            pagination = data.get("pagination", {})
            page_count = pagination.get("page_count", 1)
            if page >= page_count or not events:
                break
            # Cap at 2000 events
            if len(all_events) >= 2000:
                logger.warning("Eventbrite: capped at 2000 events")
                break
            page += 1

        logger.info(f"Eventbrite: fetched {len(all_events)} events")
        return all_events

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=2, max=10),
    )
    def _fetch_page(self, page: int, start: str, end: str) -> dict:
        params = {
            "location.address": "New York, NY",
            "location.within": "10mi",
            "categories": MUSIC_CATEGORY_ID,
            "start_date.range_start": start,
            "start_date.range_end": end,
            "expand": "venue,organizer",
            "page_size": PAGE_SIZE,
            "page": page,
            "sort_by": "date",
        }
        resp = self._client.get(BASE_URL, params=params)
        resp.raise_for_status()
        logger.debug(f"Eventbrite page {page}: HTTP {resp.status_code}")
        return resp.json()

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
