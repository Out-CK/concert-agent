"""
TikTokParser — converts raw TikTok video records into structured EventEntry objects.

Each "record" is a dict with at minimum:
    caption        : str   (video caption text)
    creator_handle : str   (TikTok @handle, without the @)
    post_id        : str   (numeric video ID)
    description    : str   (full description from video page, if fetched; else same as caption)
    posted_at      : str   (ISO date string when video was posted)
"""
from __future__ import annotations

from typing import List, Optional

from langchain_anthropic import ChatAnthropic
from pydantic import BaseModel

from agent.web_batch_parser import EventEntry
from utils.logger import get_logger

logger = get_logger(__name__)

MODEL = "claude-sonnet-4-6"
BATCH_SIZE = 10  # TikTok captions are short — process more per call

SYSTEM_PROMPT = """You are a concert data extraction specialist. You will receive a list of TikTok
videos (captions, descriptions, and source URLs). Extract all upcoming NYC concert events mentioned.

Rules:
- Create a SEPARATE entry for each distinct concert date.
- Only extract events that are clearly UPCOMING (future dates relative to today). Skip past events.
- Only extract events happening in NYC (Manhattan, Brooklyn, Queens, Bronx, Staten Island).
- Set event_type = "concert" always. Skip sports, theater, comedy, film, and conferences.
- event_title format: "[Artist] at [Venue]"
- date format: "MM-DD-YYYY" (e.g., "06-15-2026")
- start_time / end_time format: "00:00am" or "00:00pm" (e.g., "08:00pm")
- Populate no_tickets_source_1 with the TikTok video URL (https://www.tiktok.com/@{handle}/video/{post_id}).
- Populate no_tickets_webpage_contents_1 with the caption/description text.
- If the caption contains a ticket link (e.g. axs.com, ticketmaster.com, stubhub.com, dice.fm),
  use tickets_source_1 for the TikTok URL and tickets_webpage_contents_1 for the text instead.
- If artist, venue, OR date cannot be confidently extracted from the text, SKIP that entry.
- DO NOT set event_entry_id or entry_batch_id — leave them as empty strings "".
- Return a JSON object with key "entries" containing an array of EventEntry objects.
- Be conservative: only extract events you are highly confident about.
"""


class EntryList(BaseModel):
    entries: List[EventEntry]


class TikTokParser:
    def __init__(self):
        self._llm = ChatAnthropic(model=MODEL).with_structured_output(EntryList)

    def parse(self, records: list[dict]) -> list[EventEntry]:
        """Parse a list of TikTok video records into EventEntry objects."""
        logger.info(f"TikTokParser processing {len(records)} videos in batches of {BATCH_SIZE}…")
        all_entries: list[EventEntry] = []

        for batch_start in range(0, len(records), BATCH_SIZE):
            batch = records[batch_start: batch_start + BATCH_SIZE]
            try:
                entries = self._parse_batch(batch)
                logger.info(
                    f"Batch {batch_start}–{batch_start + len(batch)}: parsed {len(entries)} entries"
                )
                all_entries.extend(entries)
            except Exception as e:
                logger.error(
                    f"TikTokParser batch {batch_start}–{batch_start + len(batch)} failed: {e}"
                )

        logger.info(f"TikTokParser total entries parsed: {len(all_entries)}")
        return all_entries

    def _parse_batch(self, batch: list[dict]) -> list[EventEntry]:
        videos_text = ""
        for rec in batch:
            handle = rec.get("creator_handle", "unknown")
            post_id = rec.get("post_id", "")
            caption = rec.get("description") or rec.get("caption") or ""
            posted_at = rec.get("posted_at", "")
            tiktok_url = f"https://www.tiktok.com/@{handle}/video/{post_id}" if post_id else ""

            videos_text += (
                f"\n\n---\n"
                f"TIKTOK URL: {tiktok_url}\n"
                f"CREATOR: @{handle}\n"
                f"POSTED AT: {posted_at}\n"
                f"CAPTION/DESCRIPTION:\n{caption[:3000]}"
            )

        result: EntryList = self._llm.invoke(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Today's date is {_today_str()}. Extract NYC concert entries from these TikTok videos:{videos_text}",
                },
            ]
        )
        return result.entries or []


def _today_str() -> str:
    from datetime import date
    return date.today().strftime("%m-%d-%Y")
