"""
InstagramPostParser — parses scraped Instagram profile content into EventEntry objects.
"""
from __future__ import annotations

from datetime import date

from langchain_anthropic import ChatAnthropic

from agent.web_batch_parser import EntryList, EventEntry
from utils.logger import get_logger

logger = get_logger(__name__)

MODEL = "claude-sonnet-4-6"
BATCH_SIZE = 4  # profiles per LLM call (Instagram content can be verbose)

SYSTEM_PROMPT_TEMPLATE = """You are a concert data extraction specialist analyzing scraped Instagram profiles.
Your job is to find upcoming NYC concert events announced in the posts and captions.

Today's date is {today}. Only extract concerts with dates AFTER today.

Rules:
- Extract only concerts taking place in New York City (Manhattan, Brooklyn, Queens, Bronx, Staten Island).
- Create a SEPARATE entry for each distinct concert date.
- Set event_type = "concert". Skip sports, comedy shows, theater, films, club DJ nights with no live performer.
- event_title format: "[Artist] at [Venue]"
- date format: "MM-DD-YYYY" (e.g. "06-15-2026")
- start_time / end_time format: "00:00am" or "00:00pm" (e.g. "08:00pm")
- If artist, venue, OR date cannot be confidently determined, SKIP that entry entirely.
- For ticket links found in post captions or bios, populate tickets_source_1 with the URL.
  Otherwise use no_tickets_source_1 with the Instagram profile URL as the source.
- DO NOT set event_entry_id or entry_batch_id — leave them as empty strings "".
- Return JSON with key "entries" containing an array of EventEntry objects.

Instagram-specific guidance:
- Post captions often abbreviate venue names — try to resolve them (e.g. "MSG" → "Madison Square Garden").
- Dates may appear as "June 15", "6/15", "06.15" — convert them all to MM-DD-YYYY using {year} as the year
  unless the post clearly states a different year.
- If a post mentions "TONIGHT" or "THIS FRIDAY", use today's date or the next matching weekday.
- Ticket links in bio or captions (linktr.ee, ticketmaster.com, etc.) should go in tickets_source_1.
"""


class InstagramPostParser:
    def __init__(self):
        today = date.today()
        self._system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            today=today.strftime("%m-%d-%Y"),
            year=today.year,
        )
        self._llm = ChatAnthropic(model=MODEL).with_structured_output(EntryList)

    def parse(self, post_pages: list[dict]) -> list[EventEntry]:
        """
        Parse a list of scraped Instagram profile dicts into EventEntry objects.
        Each dict should have 'url' and 'content' keys.
        """
        logger.info(
            f"InstagramPostParser: processing {len(post_pages)} profiles "
            f"in batches of {BATCH_SIZE}"
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

        logger.info(f"InstagramPostParser total entries: {len(all_entries)}")
        return all_entries

    def _parse_batch(self, batch: list[dict]) -> list[EventEntry]:
        pages_text = ""
        for record in batch:
            content_snippet = (record.get("content") or "")[:6000]
            pages_text += (
                f"\n\n---\n"
                f"INSTAGRAM PROFILE URL: {record.get('url', '')}\n"
                f"SCRAPED CONTENT:\n{content_snippet}"
            )

        result: EntryList = self._llm.invoke([
            {"role": "system", "content": self._system_prompt},
            {
                "role": "user",
                "content": (
                    "Extract upcoming NYC concert entries from these Instagram profiles:"
                    + pages_text
                ),
            },
        ])
        entries = result.entries or []

        # Attach the source Instagram profile URL to entries that lack a source
        for entry in entries:
            if not entry.no_tickets_source_1 and not entry.tickets_source_1:
                for record in batch:
                    if record.get("url"):
                        entry.no_tickets_source_1 = record["url"]
                        entry.no_tickets_webpage_contents_1 = (
                            (record.get("content") or "")[:10000]
                        )
                        break

        return entries
