"""
Ticketmaster Discovery API client.

Docs: https://developer.ticketmaster.com/products-and-docs/apis/discovery-api/v2/
Requires: TICKETMASTER_API_KEY env var (free — register at developer.ticketmaster.com)
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Iterator

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from utils.logger import get_logger

logger = get_logger(__name__)

BASE_URL = "https://app.ticketmaster.com/discovery/v2/events.json"
PAGE_SIZE = 200  # max allowed


class TicketmasterClient:
    def __init__(self):
        self._api_key = os.environ["TICKETMASTER_API_KEY"]
        self._client = httpx.Client(timeout=30)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def fetch_nyc_concerts(self, days_ahead: int = 90) -> list[dict]:
        """Return all upcoming NYC music events within the next `days_ahead` days."""
        now = datetime.now(tz=timezone.utc)
        start = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        end = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%dT%H:%M:%SZ")

        all_events: list[dict] = []
        for page_events in self._paginate(start, end):
            all_events.extend(page_events)

        logger.info(f"Ticketmaster: fetched {len(all_events)} events")
        return all_events

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _paginate(self, start: str, end: str) -> Iterator[list[dict]]:
        page = 0
        while True:
            data = self._fetch_page(page, start, end)
            embedded = data.get("_embedded", {})
            events = embedded.get("events", [])
            yield events

            page_meta = data.get("page", {})
            total_pages = page_meta.get("totalPages", 1)
            if page >= total_pages - 1 or not events:
                break
            page += 1

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=2, max=10),
    )
    def _fetch_page(self, page: int, start: str, end: str) -> dict:
        params = {
            "apikey": self._api_key,
            "classificationName": "music",
            "city": "New York",
            "stateCode": "NY",
            "countryCode": "US",
            "size": PAGE_SIZE,
            "page": page,
            "startDateTime": start,
            "endDateTime": end,
            "sort": "date,asc",
        }
        resp = self._client.get(BASE_URL, params=params)
        resp.raise_for_status()
        logger.debug(f"Ticketmaster page {page}: HTTP {resp.status_code}")
        return resp.json()

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
