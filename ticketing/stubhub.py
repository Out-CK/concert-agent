"""
StubHub Catalog API client.

Docs: https://developers.stubhub.com/
Requires:
  STUBHUB_CLIENT_ID     — from developer.stubhub.com app registration
  STUBHUB_CLIENT_SECRET — from developer.stubhub.com app registration

Uses the OAuth2 Client Credentials flow to obtain a bearer token, then queries
the StubHub catalog event search API for upcoming NYC concerts.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from utils.logger import get_logger

logger = get_logger(__name__)

TOKEN_URL = "https://account.stubhub.com/oauth2/token"
SEARCH_URL = "https://api.stubhub.com/catalog/events/v1"
PAGE_SIZE = 100


class StubHubClient:
    def __init__(self):
        self._client_id = os.environ["STUBHUB_CLIENT_ID"]
        self._client_secret = os.environ["STUBHUB_CLIENT_SECRET"]
        self._http = httpx.Client(timeout=30)
        self._token: Optional[str] = None

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def fetch_nyc_concerts(self, days_ahead: int = 90) -> list[dict]:
        """Return upcoming NYC concert events within the next `days_ahead` days."""
        self._ensure_token()
        now = datetime.now(tz=timezone.utc)
        start = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        end = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%dT%H:%M:%SZ")

        all_events: list[dict] = []
        start_idx = 0
        while True:
            data = self._fetch_page(start_idx, start, end)
            events = data.get("events", []) or []
            all_events.extend(events)

            total = data.get("totalResults", 0) or data.get("numFound", 0)
            if not events or len(all_events) >= total or len(all_events) >= 2000:
                break
            start_idx += PAGE_SIZE

        logger.info(f"StubHub: fetched {len(all_events)} events")
        return all_events

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _ensure_token(self) -> None:
        if self._token:
            return
        resp = self._http.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "scope": "read:events",
            },
        )
        resp.raise_for_status()
        self._token = resp.json()["access_token"]
        logger.debug("StubHub: OAuth2 token obtained")

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=2, max=10),
    )
    def _fetch_page(self, start_idx: int, start: str, end: str) -> dict:
        params = {
            "city": "New York",
            "country": "US",
            "state": "NY",
            "genreName": "Concert",
            "dateLocal.gte": start,
            "dateLocal.lte": end,
            "rows": PAGE_SIZE,
            "start": start_idx,
            "sort": "dateLocal asc",
        }
        resp = self._http.get(
            SEARCH_URL,
            params=params,
            headers={"Authorization": f"Bearer {self._token}"},
        )
        resp.raise_for_status()
        logger.debug(f"StubHub offset {start_idx}: HTTP {resp.status_code}")
        return resp.json()

    def close(self):
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
