"""
InstagramPostParser — converts raw Instagram profile content into EventEntry objects.

Each record in post_pages is a dict with:
    url     : str   (Instagram profile URL)
    handle  : str   (Instagram handle)
    content : str   (combined bio + post captions, up to 30000 chars)
"""
from __future__ import annotations

from datetime import date
from typing import List

from langchain_anthropic import ChatAnthropic
from pydantic import BaseModel

from agent.web_batch_parser import EventEntry
from utils.logger import get_logger

logger = get_logger(__name__)

MODEL = "claude-sonnet-4-6"
BATCH_SIZE = 5

SYSTEM_PROMPT = """You are a concert data extraction specialist. You will receive content from
NYC venue and promoter Instagram profiles — biographies and post captions.
Extract all upcoming NYC concert events mentioned.

Rules:
- Create a SEPARATE entry for each distinct concert date.
- Only extract events that are clearly UPCOMING (future dates relative to today). Skip past events.
- Only extract events happening in NYC (Manhattan, Brooklyn, Queens, Bronx, Staten Island).
- Set event_type = "concert" always. Skip art exhibitions, theater, comedy, film, and conferences.
- event_title format: "[Artist] at [Venue]"
- date format: "MM-DD-YYYY" (e.g., "06-15-2026")
- start_time / end_time format: "00:00am" or "00:00pm" (e.g., "08:00pm")
- Populate no_tickets_source_1 with the Instagram profile URL.
- Populate no_tickets_webpage_contents_1 with the relevant post caption text.
- If a post contains a ticket link (axs.com, ticketmaster.com, stubhub.com, dice.fm, etc.),
  use tickets_source_1 for the Instagram URL and tickets_webpage_contents_1 for the text instead.
- If artist, venue, OR date cannot be confidently extracted, SKIP that entry.
- DO NOT set event_entry_id or entry_batch_id — leave them as empty strings "".
- Return a JSON object with key "entries" containing an array of EventEntry objects.
- Be conservative: only extract events you are highly confident about.
"""


class EntryList(BaseModel):
    entries: List[EventEntry]


class InstagramPostParser:
    def __init__(self):
        self._llm = ChatAnthropic(model=MODEL).with_structured_output(EntryList)

    def parse(self, post_pages: list[dict]) -> list[EventEntry]:
        """Parse a list of Instagram profile content records into EventEntry objects."""
        logger.info(
            f"InstagramPostParser processing {len(post_pages)} profiles "
            f"in batches of {BATCH_SIZE}…"
        )
        all_entries: list[EventEntry] = []

        for batch_start in range(0, len(post_pages), BATCH_SIZE):
            batch = post_pages[batch_start: batch_start + BATCH_SIZE]
            try:
                entries = self._parse_batch(batch)
                logger.info(
                    f"Batch {batch_start}–{batch_start + len(batch)}: "
                    f"parsed {len(entries)} entries"
                )
                all_entries.extend(entries)
            except Exception as e:
                logger.error(
                    f"InstagramPostParser batch "
                    f"{batch_start}–{batch_start + len(batch)} failed: {e}"
                )

        logger.info(f"InstagramPostParser total entries parsed: {len(all_entries)}")
        return all_entries

    def _parse_batch(self, batch: list[dict]) -> list[EventEntry]:
        pages_text = ""
        for record in batch:
            pages_text += (
                f"\n\n---\n"
                f"INSTAGRAM PROFILE: @{record.get('handle', '')}\n"
                f"URL: {record.get('url', '')}\n"
                f"CONTENT:\n{record.get('content', '')[:5000]}"
            )

        result: EntryList = self._llm.invoke([
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Today's date is {date.today().strftime('%m-%d-%Y')}. "
                    f"Extract NYC concert entries from these Instagram profiles:{pages_text}"
                ),
            },
        ])
        return result.entries or []
