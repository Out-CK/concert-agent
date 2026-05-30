"""
Convert raw API response dicts from each ticketing platform into EventEntry objects.
Each from_* function returns an EventEntry or None if the event is unusable.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from agent.web_batch_parser import EventEntry

_AT_RE = re.compile(r"^(.+?)\s+(?:at|@|[-–—])\s+.+$", re.IGNORECASE)


def _fmt_date(date_str: str) -> str:
    """YYYY-MM-DD → MM-DD-YYYY."""
    try:
        dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
        return dt.strftime("%m-%d-%Y")
    except (ValueError, TypeError):
        return date_str


def _fmt_time(time_str: str) -> Optional[str]:
    """HH:MM or HH:MM:SS → HH:MMam/pm (e.g. '08:00pm')."""
    if not time_str:
        return None
    try:
        t = datetime.strptime(time_str[:5], "%H:%M")
        return t.strftime("%I:%M%p").lower().lstrip("0") or "12:00am"
    except (ValueError, TypeError):
        return None


def _fmt_dt(dt: datetime) -> tuple[str, str]:
    """Return (date MM-DD-YYYY, time HH:MMam/pm) from a datetime."""
    return dt.strftime("%m-%d-%Y"), (dt.strftime("%I:%M%p").lower().lstrip("0") or "12:00am")


def _build_address(line1: str, city: str, state: str, postal: str) -> Optional[str]:
    parts = [p.strip() for p in [line1, city, f"{state} {postal}".strip()] if p and p.strip()]
    return ", ".join(parts) if parts else None


def _artist_from_title(title: str) -> str:
    """Best-effort: extract artist from a title like 'Artist at Venue'."""
    m = _AT_RE.match(title)
    return m.group(1).strip() if m else title


# ---------------------------------------------------------------------------
# Ticketmaster
# ---------------------------------------------------------------------------

def from_ticketmaster(event: dict) -> Optional[EventEntry]:
    try:
        embedded = event.get("_embedded", {})
        venues = embedded.get("venues", [])
        attractions = embedded.get("attractions", [])

        event_name = event.get("name", "").strip()
        venue_info = venues[0] if venues else {}
        venue_name = venue_info.get("name", "").strip()

        artist_name = attractions[0].get("name", "").strip() if attractions else ""
        if not artist_name:
            artist_name = _artist_from_title(event_name) or event_name

        dates = event.get("dates", {}).get("start", {})
        date_str = dates.get("localDate", "")
        time_str = dates.get("localTime", "")  # "20:00:00"

        if not (artist_name and venue_name and date_str):
            return None

        addr = venue_info.get("address", {})
        address = _build_address(
            addr.get("line1", ""),
            (venue_info.get("city") or {}).get("name", ""),
            (venue_info.get("state") or {}).get("stateCode", ""),
            venue_info.get("postalCode", ""),
        )

        url = event.get("url", "")
        title = f"{artist_name} at {venue_name}"
        description = event.get("info") or event.get("description") or (
            f"{artist_name} performing live at {venue_name}"
        )

        return EventEntry(
            event_title=title,
            description=str(description)[:500],
            artist=artist_name,
            venue=venue_name,
            event_type="concert",
            multi_day_event=False,
            date=_fmt_date(date_str),
            start_time=_fmt_time(time_str),
            tickets_source_1=url or None,
            tickets_webpage_contents_1=str(description)[:500] if url else None,
            address=address,
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# SeatGeek
# ---------------------------------------------------------------------------

def from_seatgeek(event: dict) -> Optional[EventEntry]:
    try:
        venue_info = event.get("venue", {}) or {}
        performers = event.get("performers", [])

        venue_name = venue_info.get("name", "").strip()
        artist_name = performers[0].get("name", "").strip() if performers else ""
        if not artist_name:
            artist_name = _artist_from_title(event.get("short_title", "") or event.get("title", ""))

        dt_str = event.get("datetime_local", "")
        if not dt_str:
            return None

        dt = datetime.fromisoformat(dt_str)
        date_formatted, time_formatted = _fmt_dt(dt)

        if not (artist_name and venue_name):
            return None

        address = _build_address(
            venue_info.get("address", ""),
            venue_info.get("city", ""),
            venue_info.get("state", ""),
            str(venue_info.get("postal_code", "") or ""),
        )

        url = event.get("url", "")
        title = f"{artist_name} at {venue_name}"
        description = f"{artist_name} performing live at {venue_name}"

        return EventEntry(
            event_title=title,
            description=description,
            artist=artist_name,
            venue=venue_name,
            event_type="concert",
            multi_day_event=False,
            date=date_formatted,
            start_time=time_formatted,
            tickets_source_1=url or None,
            tickets_webpage_contents_1=description if url else None,
            address=address,
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Eventbrite
# ---------------------------------------------------------------------------

def from_eventbrite(event: dict) -> Optional[EventEntry]:
    try:
        venue_info = event.get("venue", {}) or {}
        name_info = event.get("name", {}) or {}
        event_name = (name_info.get("text") or name_info.get("html") or "").strip()
        venue_name = venue_info.get("name", "").strip()

        artist_name = _artist_from_title(event_name) or event_name

        start_info = event.get("start", {}) or {}
        end_info = event.get("end", {}) or {}
        dt_str = start_info.get("local", "")
        end_str = end_info.get("local", "")

        if not dt_str:
            return None

        dt = datetime.fromisoformat(dt_str)
        date_formatted, time_formatted = _fmt_dt(dt)

        end_time_formatted = None
        if end_str:
            end_dt = datetime.fromisoformat(end_str)
            end_time_formatted = _fmt_dt(end_dt)[1]

        if not (artist_name and venue_name):
            return None

        addr_info = venue_info.get("address", {}) or {}
        address = (
            addr_info.get("localized_address_display")
            or _build_address(
                addr_info.get("address_1", ""),
                addr_info.get("city", ""),
                addr_info.get("region", ""),
                addr_info.get("postal_code", ""),
            )
        )

        url = event.get("url", "")
        title = f"{artist_name} at {venue_name}"
        desc_info = event.get("description", {}) or {}
        description = (desc_info.get("text") or f"{artist_name} performing live at {venue_name}")[:500]

        return EventEntry(
            event_title=title,
            description=description,
            artist=artist_name,
            venue=venue_name,
            event_type="concert",
            multi_day_event=False,
            date=date_formatted,
            start_time=time_formatted,
            end_time=end_time_formatted,
            tickets_source_1=url or None,
            tickets_webpage_contents_1=description if url else None,
            address=address or None,
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# StubHub
# ---------------------------------------------------------------------------

def from_stubhub(event: dict) -> Optional[EventEntry]:
    try:
        name = (event.get("name") or event.get("title") or "").strip()
        venue_info = event.get("venue", {}) or {}
        venue_name = venue_info.get("name", "").strip() or event.get("venueName", "").strip()

        performers = event.get("performers", []) or event.get("acts", [])
        artist_name = performers[0].get("name", "").strip() if performers else ""
        if not artist_name:
            artist_name = _artist_from_title(name) or name

        date_str = event.get("eventDateLocal", "") or event.get("eventDate", "") or event.get("date", "")
        if not date_str:
            return None

        dt = datetime.fromisoformat(date_str[:19])
        date_formatted, time_formatted = _fmt_dt(dt)

        if not (artist_name and venue_name):
            return None

        address = _build_address(
            venue_info.get("address1", "") or venue_info.get("address", ""),
            venue_info.get("city", ""),
            venue_info.get("state", ""),
            venue_info.get("postalCode", "") or venue_info.get("zip", ""),
        )

        event_id = event.get("id", "")
        url = event.get("url", "") or (f"https://www.stubhub.com/event/{event_id}" if event_id else "")
        title = f"{artist_name} at {venue_name}"
        description = f"{artist_name} performing live at {venue_name}"

        return EventEntry(
            event_title=title,
            description=description,
            artist=artist_name,
            venue=venue_name,
            event_type="concert",
            multi_day_event=False,
            date=date_formatted,
            start_time=time_formatted,
            tickets_source_1=url or None,
            tickets_webpage_contents_1=description if url else None,
            address=address or None,
        )
    except Exception:
        return None
