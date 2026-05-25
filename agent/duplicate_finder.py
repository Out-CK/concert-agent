from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

from langchain_anthropic import ChatAnthropic
from pydantic import BaseModel

from db.operations import get_existing_future_entries
from agent.web_batch_parser import EventEntry
from utils.logger import get_logger

logger = get_logger(__name__)

MODEL = "claude-sonnet-4-20250514"

MERGE_SYSTEM_PROMPT = """You are comparing two or more concert event entries that refer to the same
show (same artist, venue, and date). Select the BEST version of event_title and description
from the candidates. Return a JSON object with keys "event_title" and "description"."""


class MergeChoice(BaseModel):
    event_title: str
    description: str


class DuplicateFinder:
    def __init__(self, id_generator):
        self._llm = ChatAnthropic(model=MODEL).with_structured_output(MergeChoice)
        self._id_gen = id_generator

    # ------------------------------------------------------------------
    # Operation A — Intra-Batch Deduplication
    # ------------------------------------------------------------------

    def deduplicate_batch(self, entries: list[EventEntry]) -> list[EventEntry]:
        """Group by (artist, venue, date) and merge duplicates."""
        logger.info(f"Intra-batch dedup: starting with {len(entries)} entries")
        groups: dict[tuple, list[EventEntry]] = defaultdict(list)
        for entry in entries:
            key = (entry.artist.strip().lower(), entry.venue.strip().lower(), entry.date.strip())
            groups[key].append(entry)

        deduplicated: list[EventEntry] = []
        removed = 0
        for key, group in groups.items():
            if len(group) == 1:
                deduplicated.append(group[0])
            else:
                merged = self._merge_group(group)
                deduplicated.append(merged)
                removed += len(group) - 1
                logger.info(f"Merged {len(group)} duplicates for {key}")

        logger.info(
            f"Intra-batch dedup complete: {len(deduplicated)} entries remain, {removed} removed"
        )
        return deduplicated

    def _merge_group(self, group: list[EventEntry]) -> EventEntry:
        # Pick titles/descriptions via LLM
        candidates_text = "\n\n".join(
            f"Candidate {i + 1}:\n  event_title: {e.event_title}\n  description: {e.description}"
            for i, e in enumerate(group)
        )
        try:
            choice: MergeChoice = self._llm.invoke(
                [
                    {"role": "system", "content": MERGE_SYSTEM_PROMPT},
                    {"role": "user", "content": candidates_text},
                ]
            )
            best_title = choice.event_title
            best_desc = choice.description
        except Exception as e:
            logger.warning(f"LLM merge failed, using first entry values: {e}")
            best_title = group[0].event_title
            best_desc = group[0].description

        base = group[0]
        merged_dict: dict[str, Any] = {
            "event_entry_id": self._id_gen.next(),
            "entry_batch_id": base.entry_batch_id,
            "event_title": best_title,
            "description": best_desc,
            "artist": base.artist,
            "venue": base.venue,
            "event_type": base.event_type,
            "multi_day_event": base.multi_day_event,
            "date": base.date,
            "start_time": base.start_time,
            "end_time": base.end_time,
            "webpage_contents": base.webpage_contents,
        }

        # Merge source slots
        merged_dict.update(self._merge_source_slots(group, "tickets"))
        merged_dict.update(self._merge_source_slots(group, "no_tickets"))

        return EventEntry(**merged_dict)

    def _merge_source_slots(self, group: list[EventEntry], prefix: str) -> dict[str, Optional[str]]:
        """Collect all source/content pairs from a duplicate group into numbered slots (max 4)."""
        urls: list[str] = []
        contents: list[str] = []

        for entry in group:
            for slot in range(1, 5):
                url = getattr(entry, f"{prefix}_source_{slot}", None)
                content = getattr(entry, f"{prefix}_webpage_contents_{slot}", None)
                if url and url not in urls:
                    urls.append(url)
                    contents.append(content or "")

        if len(urls) > 4:
            logger.warning(
                f"Merge overflow: {len(urls)} {prefix} sources found, keeping first 4"
            )
            urls = urls[:4]
            contents = contents[:4]

        result: dict[str, Optional[str]] = {}
        for slot in range(1, 5):
            if slot <= len(urls):
                result[f"{prefix}_source_{slot}"] = urls[slot - 1]
                result[f"{prefix}_webpage_contents_{slot}"] = contents[slot - 1]
            else:
                result[f"{prefix}_source_{slot}"] = None
                result[f"{prefix}_webpage_contents_{slot}"] = None
        return result

    # ------------------------------------------------------------------
    # Operation B — Cross-Reference Against Event Entry Database
    # ------------------------------------------------------------------

    def cross_reference_db(self, entries: list[EventEntry]) -> list[EventEntry]:
        """Remove entries that already exist in the Event Entry Database."""
        logger.info(f"Cross-DB dedup: checking {len(entries)} entries against DB…")
        existing = get_existing_future_entries()
        existing_keys: set[tuple] = {
            (r["artist"].strip().lower(), r["venue"].strip().lower(), r["date"].strip())
            for r in existing
        }
        existing_id_map: dict[tuple, str] = {
            (r["artist"].strip().lower(), r["venue"].strip().lower(), r["date"].strip()): r["event_entry_id"]
            for r in existing
        }

        net_new: list[EventEntry] = []
        removed = 0
        for entry in entries:
            key = (entry.artist.strip().lower(), entry.venue.strip().lower(), entry.date.strip())
            if key in existing_keys:
                db_id = existing_id_map.get(key, "unknown")
                logger.info(
                    f"Cross-DB duplicate: {entry.event_entry_id} matches DB entry {db_id} — skipping"
                )
                removed += 1
            else:
                net_new.append(entry)

        logger.info(f"Cross-DB dedup: {len(net_new)} net-new entries, {removed} removed")
        return net_new
