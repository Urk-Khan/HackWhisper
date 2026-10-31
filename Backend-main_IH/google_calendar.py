"""
⚠️  CURRENTLY INACTIVE — replaced by cal_com.py

This file is preserved so you can re-enable the Google Calendar flow once you have
a Google Cloud service account. To switch back, see the instructions at the top of
google_sheets.py.

"""
"""
Google Calendar integration — availability checking and meeting creation with an
auto-generated Google Meet link.

Same auth pattern as google_sheets.py (service account via google-auth), different scope
("calendar" instead of "spreadsheets"). Uses two Google Calendar API v3 endpoints:

  - freeBusy.query — checks whether a specific time window is actually free on the rep's
    calendar (catches things a spreadsheet can't: PTO, a call booked outside this system,
    etc.)
  - events.insert with conferenceData — this is how a Google Meet link actually gets
    created. There's no separate "Meet API" call; passing conferenceData.createRequest on
    the event insert is what generates it, and requires ?conferenceDataVersion=1 on the
    request or Google silently ignores the conference request and returns no link.

Sales and Support can use the same calendar (set both env vars to the same ID) or different
ones (e.g. if different people handle each) — this module is agnostic, it just takes
whichever calendar_id it's given.

Slot format assumption: dates from google_sheets.py are "YYYY-MM-DD" and times are
"H:MM AM/PM" (e.g. "2:00 PM") — see _parse_slot_datetime below. If your Sheet uses a
different format, that's the one function to change.
"""

import asyncio
import os
import time
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import aiohttp
from loguru import logger

TIMEZONE = os.getenv("TIMEZONE", "UTC")
MEETING_DURATION_MINUTES = int(os.getenv("MEETING_DURATION_MINUTES", "30"))
CREDENTIALS_PATH = os.getenv("GOOGLE_SHEETS_CREDENTIALS_PATH", "")  # same service account as Sheets

# Sales and Support can use the same calendar (set both env vars to the same ID) or
# different ones (e.g. if different people handle each).
CALENDAR_IDS = {
    "sales": os.getenv("GOOGLE_CALENDAR_ID_SALES", ""),
    "support": os.getenv("GOOGLE_CALENDAR_ID_SUPPORT", ""),
}


def resolve_calendar_id(meeting_type: str) -> str:
    return CALENDAR_IDS.get(meeting_type.lower(), CALENDAR_IDS["sales"])


_CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=8)

_cached_token: str | None = None
_cached_token_expiry: float = 0.0
_token_lock = asyncio.Lock()


def _refresh_token_sync() -> tuple[str, float]:
    """Blocking (network + crypto) — always run via asyncio.to_thread."""
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(
        CREDENTIALS_PATH, scopes=["https://www.googleapis.com/auth/calendar"]
    )
    creds.refresh(Request())
    return creds.token, creds.expiry.replace(tzinfo=ZoneInfo("UTC")).timestamp()


async def _get_access_token() -> str | None:
    global _cached_token, _cached_token_expiry

    if not CREDENTIALS_PATH:
        logger.error("GOOGLE_SHEETS_CREDENTIALS_PATH not set — google_calendar.py reuses it")
        return None

    async with _token_lock:
        now = time.time()
        if _cached_token and now < _cached_token_expiry - 60:
            return _cached_token
        try:
            token, expiry = await asyncio.to_thread(_refresh_token_sync)
        except Exception as e:
            logger.error(f"Failed to get Google Calendar access token: {e}")
            return None
        _cached_token, _cached_token_expiry = token, expiry
        return token


def _parse_slot_datetime(date: str, time_str: str) -> datetime | None:
    """"2026-09-15" + "2:00 PM" -> an aware datetime in TIMEZONE. Returns None on a format
    mismatch rather than raising — a single malformed Sheet row shouldn't crash the whole
    availability check."""
    try:
        naive = datetime.strptime(f"{date} {time_str}", "%Y-%m-%d %I:%M %p")
        return naive.replace(tzinfo=ZoneInfo(TIMEZONE))
    except ValueError as e:
        logger.warning(f"Could not parse slot date/time '{date} {time_str}': {e}")
        return None


async def is_slot_free(*, calendar_id: str, date: str, time_str: str) -> bool:
    """True if the given date/time (interpreted as a MEETING_DURATION_MINUTES-long window)
    has no conflicting events on calendar_id. Fails open to False (not free) on any error —
    better to skip offering a slot than to double-book because of a transient API error."""
    token = await _get_access_token()
    if not token or not calendar_id:
        return False

    start = _parse_slot_datetime(date, time_str)
    if not start:
        return False
    end = start + timedelta(minutes=MEETING_DURATION_MINUTES)

    url = f"{_CALENDAR_API_BASE}/freeBusy"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {
        "timeMin": start.isoformat(),
        "timeMax": end.isoformat(),
        "items": [{"id": calendar_id}],
    }

    try:
        async with aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT) as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status >= 400:
                    logger.error(f"freeBusy check failed ({resp.status}): {await resp.text()}")
                    return False
                data = await resp.json()
    except Exception as e:
        logger.error(f"freeBusy request failed: {e}")
        return False

    busy_periods = data.get("calendars", {}).get(calendar_id, {}).get("busy", [])
    return len(busy_periods) == 0


async def create_meeting_event(
    *,
    calendar_id: str,
    date: str,
    time_str: str,
    summary: str,
    description: str,
    attendee_email: str | None,
) -> dict:
    """Creates the calendar event AND generates a Google Meet link in one call. Returns
    {"success": bool, "meet_link": str | None, "event_id": str | None, "error": str | None}."""
    token = await _get_access_token()
    if not token or not calendar_id:
        return {"success": False, "meet_link": None, "event_id": None, "error": "calendar_not_configured"}

    start = _parse_slot_datetime(date, time_str)
    if not start:
        return {"success": False, "meet_link": None, "event_id": None, "error": "invalid_datetime"}
    end = start + timedelta(minutes=MEETING_DURATION_MINUTES)

    attendees = [{"email": attendee_email}] if attendee_email else []

    payload = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start.isoformat(), "timeZone": TIMEZONE},
        "end": {"dateTime": end.isoformat(), "timeZone": TIMEZONE},
        "attendees": attendees,
        "conferenceData": {
            "createRequest": {
                "requestId": str(uuid.uuid4()),
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        },
    }

    # conferenceDataVersion=1 is required or the createRequest above is silently ignored and
    # no Meet link gets generated — this is the one detail that's easy to miss here.
    url = f"{_CALENDAR_API_BASE}/calendars/{calendar_id}/events?conferenceDataVersion=1"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    try:
        async with aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT) as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    logger.error(f"Calendar event creation failed ({resp.status}): {body}")
                    return {"success": False, "meet_link": None, "event_id": None, "error": "event_creation_failed"}
                event = await resp.json()
    except Exception as e:
        logger.error(f"Calendar event creation request failed: {e}")
        return {"success": False, "meet_link": None, "event_id": None, "error": "calendar_unreachable"}

    meet_link = None
    for entry_point in event.get("conferenceData", {}).get("entryPoints", []):
        if entry_point.get("entryPointType") == "video":
            meet_link = entry_point.get("uri")
            break

    if not meet_link:
        logger.warning(f"Event created but no Meet link came back: {event.get('id')}")

    return {"success": True, "meet_link": meet_link, "event_id": event.get("id"), "error": None}
