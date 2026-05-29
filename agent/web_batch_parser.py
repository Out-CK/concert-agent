from __future__ import annotations

from typing import List, Optional

from langchain_anthropic import ChatAnthropic
from pydantic import BaseModel

from utils.logger import get_logger

logger = get_logger(__name__)

MODEL = "claude-sonnet-4-6"
BATCH_SIZE = 3

SYSTEM_PROMPT = """You are a concert data extraction specialist. For each web page provided,
extract all individual NYC concert events mentioned.

Rules:
- Create a SEPARATE entry for each distinct concert date found on a page.
- For multi-day events (festival spanning multiple days, or same artist playing multiple nights),
  create one entry per day — all share the same event_title but each is a separate entry.
- Set event_type = "concert" always. Skip sports, theater, comedy, films, and conferences.
- If a page has ticket purchase links, populate tickets_source_1 with the ticket URL and
  tickets_webpage_contents_1 with that page's content snippet. Otherwise use no_tickets_source_1.
- event_title format: "[Artist] at [Venue]" for single-artist, "[Festival] at [Venue]" for festivals.
- date format: "MM-DD-YYYY" (e.g., "06-15-2026")
- start_time / end_time format: "00:00am" or "00:00pm" (e.g., "08:00pm")
- If the venue's full street address is visible anywhere on the page, populate the `address` field
  (e.g., "35 W 35th St, New York, NY 10001" or "51 W 30th St, Manhattan, NY 10001").
  Include the street number, street name, borough/city, state, and zip if available.
  If no street address is visible, leave `address` empty.
- If artist, venue, OR date cannot be confidently extracted, SKIP that entry.
- DO NOT set event_entry_id or entry_batch_id — leave them as empty strings "".
- Return a JSON object with key "entries" containing an array of EventEntry objects.
"""


class EventEntry(BaseModel):
    event_entry_id: str = ""
    entry_batch_id: str = ""
    event_title: str
    description: str
    artist: str
    venue: str
    event_type: str = "concert"
    multi_day_event: bool
    date: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    tickets_source_1: Optional[str] = None
    tickets_webpage_contents_1: Optional[str] = None
    tickets_source_2: Optional[str] = None
    tickets_webpage_contents_2: Optional[str] = None
    tickets_source_3: Optional[str] = None
    tickets_webpage_contents_3: Optional[str] = None
    tickets_source_4: Optional[str] = None
    tickets_webpage_contents_4: Optional[str] = None
    no_tickets_source_1: Optional[str] = None
    no_tickets_webpage_contents_1: Optional[str] = None
    no_tickets_source_2: Optional[str] = None
    no_tickets_webpage_contents_2: Optional[str] = None
    no_tickets_source_3: Optional[str] = None
    no_tickets_webpage_contents_3: Optional[str] = None
    no_tickets_source_4: Optional[str] = None
    no_tickets_webpage_contents_4: Optional[str] = None
    webpage_contents: Optional[str] = None
    address: Optional[str] = None


class EntryList(BaseModel):
    entries: List[EventEntry]


class WebBatchParser:
    def __init__(self):
        self._llm = ChatAnthropic(model=MODEL).with_structured_output(EntryList)

    def parse(self, web_batch: list[dict]) -> list[EventEntry]:
        """Parse a full Web Batch into Event Entries, processing BATCH_SIZE pages per LLM call."""
        logger.info(f"WebBatchParser processing {len(web_batch)} pages in batches of {BATCH_SIZE}…")
        all_entries: list[EventEntry] = []

        for batch_start in range(0, len(web_batch), BATCH_SIZE):
            batch = web_batch[batch_start: batch_start + BATCH_SIZE]
            try:
                entries = self._parse_batch(batch)
                logger.info(
                    f"Batch {batch_start}–{batch_start + len(batch)}: parsed {len(entries)} entries"
                )
                all_entries.extend(entries)
            except Exception as e:
                logger.error(
                    f"WebBatchParser batch {batch_start}–{batch_start + len(batch)} failed: {e}"
                )

        logger.info(f"WebBatchParser total entries parsed: {len(all_entries)}")
        return all_entries

    def _parse_batch(self, batch: list[dict]) -> list[EventEntry]:
        pages_text = ""
        for record in batch:
            content_snippet = (record.get("content") or "")[:5000]
            pages_text += (
                f"\n\n---\n"
                f"PAGE URL: {record.get('url', '')}\n"
                f"QUERY USED: {record.get('query_used', '')}\n"
                f"CONTENT:\n{content_snippet}"
            )

        result: EntryList = self._llm.invoke(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Extract concert entries from these pages:{pages_text}"},
            ]
        )
        entries = result.entries or []
        # Attach full webpage content to each entry
        for entry in entries:
            if not entry.webpage_contents:
                # Try to find the most relevant page content
                for record in batch:
                    if record.get("url"):
                        entry.webpage_contents = (record.get("content") or "")[:10000]
                        break
        return entries
