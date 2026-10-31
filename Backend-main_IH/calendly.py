"""
Calendly integration — replaces the earlier Cal.com attempt. Same plain-aiohttp pattern as
the rest of this project, no SDK.

IMPORTANT: Calendly's booking-creation endpoint (POST /invitees) returns 403 Forbidden on
their Free plan — verified directly against their own docs. You need Calendly's Standard
plan ($10/user/month) or above for this integration to work at all. This isn't a code
limitation, it's a hard restriction on Calendly's side.

Auth: a Personal Access Token (Calendly's Integrations & API settings page). Unlike
Google's service-account flow, this token doesn't expire/refresh — it's just a fixed Bearer
token from .env. Simpler than the Google or Cal.com setups this project has tried.

One-time setup (can't be done via API — Calendly's event-type creation API is limited to
one-on-one types with basic fields, and doing it by hand in the UI is more reliable):
  1. Create TWO Event Types in your Calendly account — one for Sales, one for Support.
  2. Enable a video conferencing location on each (Google Meet or Zoom) in the event type's
     location settings.
  3. Copy each event type's URI (found via the "Get Event Type" API, or by inspecting the
     event type's API details in Calendly's UI) into .env as CALENDLY_SALES_EVENT_TYPE_URI /
     CALENDLY_SUPPORT_EVENT_TYPE_URI. It looks like:
     https://api.calendly.com/event_types/AAAAAAAAAAAAAAAA

Design note vs. the earlier Sheets-based approach: slots here are identified by their exact
ISO 8601 start_time string (returned by Calendly itself), not a separately-formatted
date/time pair. tools.py must pass this start_time back to book_meeting EXACTLY as received
— same anti-reformatting principle as before, just keyed differently because Calendly's own
API is the source of truth for slot identity, not our own display formatting.
"""

import os
from datetime import datetime, timedelta, timezone

import aiohttp
from loguru import logger

CALENDLY_API_KEY = os.getenv("CALENDLY_API_KEY", "")
TIMEZONE = os.getenv("TIMEZONE", "UTC")
LOOKAHEAD_DAYS = int(os.getenv("CALENDLY_LOOKAHEAD_DAYS", "14"))  # Calendly caps this at 31

_API_BASE = "https://api.calendly.com"
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)

# Validate timezone at import time so misconfiguration is caught immediately in logs,
# not silently at booking time when the caller picks a slot.
try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    ZoneInfo(TIMEZONE)
    if TIMEZONE == "UTC":
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "TIMEZONE is set to UTC in .env — meeting slots will be displayed in UTC. "
            "If your business operates in a different timezone, set e.g. TIMEZONE=Asia/Karachi "
            "or TIMEZONE=America/New_York to show callers accurate local times."
        )
except Exception:
    import logging as _logging
    _logging.getLogger(__name__).error(
        f"TIMEZONE='{TIMEZONE}' is not a valid IANA timezone name. "
        "Defaulting display to UTC. Fix this in .env — see https://en.wikipedia.org/wiki/List_of_tz_database_time_zones"
    )
    TIMEZONE = "UTC"


def _resolve_event_type(meeting_type: str) -> str:
    sales_uri = os.getenv("CALENDLY_SALES_EVENT_TYPE_URI", "")
    support_uri = os.getenv("CALENDLY_SUPPORT_EVENT_TYPE_URI", "")
    uris = {
        "sales": sales_uri,
        "support": support_uri,
    }
    return uris.get(meeting_type.lower(), sales_uri)


def _headers() -> dict:
    api_key = os.getenv("CALENDLY_API_KEY", "")
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def format_display(start_time_iso: str) -> tuple[str, str]:
    """ISO datetime -> (date, time) display strings in TIMEZONE, for the LLM to read aloud.
    Purely cosmetic — book_meeting keys off the raw start_time, not these strings."""
    from zoneinfo import ZoneInfo

    dt = datetime.fromisoformat(start_time_iso.replace("Z", "+00:00")).astimezone(ZoneInfo(TIMEZONE))
    time_str = dt.strftime("%I:%M %p").lstrip("0") or dt.strftime("%I:%M %p")
    return dt.strftime("%Y-%m-%d"), time_str


async def get_open_slots(meeting_type: str, limit: int = 15) -> list[dict]:
    """Returns up to `limit` open slots:
    [{"start_time": "<exact ISO string>", "date": "...", "time": "..."}, ...].
    "start_time" is what must be passed back to book_meeting verbatim. "date"/"time" are
    just for the LLM to read aloud."""
    api_key = os.getenv("CALENDLY_API_KEY", "")
    lookahead_days = int(os.getenv("CALENDLY_LOOKAHEAD_DAYS", "14"))

    if not api_key:
        logger.error("CALENDLY_API_KEY not set in .env")
        return []

    event_type_uri = _resolve_event_type(meeting_type)
    if not event_type_uri:
        logger.error(f"No Calendly event type configured for meeting_type={meeting_type!r}")
        return []

    now = datetime.now(timezone.utc)
    # Add a 60-second buffer: Calendly rejects start_time values that are exactly "now"
    # with "start_time must be in the future" due to clock skew between our server and theirs.
    start_time = (now + timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
    end_time = (now + timedelta(days=lookahead_days)).isoformat().replace("+00:00", "Z")

    url = "https://api.calendly.com/event_type_available_times"
    params = {"event_type": event_type_uri, "start_time": start_time, "end_time": end_time}

    try:
        async with aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT) as session:
            async with session.get(url, params=params, headers=_headers()) as resp:
                if resp.status >= 400:
                    logger.error(f"Calendly availability check failed ({resp.status}): {await resp.text()}")
                    return []
                data = await resp.json()
    except Exception as e:
        logger.error(f"Calendly availability request failed: {e}")
        return []

    slots = []
    for item in data.get("collection", []):
        if item.get("status") != "available":
            continue
        start = item.get("start_time")
        if not start:
            continue
        date_str, time_str = format_display(start)
        slots.append({"start_time": start, "date": date_str, "time": time_str})
        if len(slots) >= limit:
            break

    return slots


async def book_slot(
    *, meeting_type: str, start_time: str, customer_name: str, phone: str, email: str, topic: str
) -> dict:
    """Books the slot at the given start_time (must be the exact ISO string from
    get_open_slots). Returns
    {"success": bool, "meet_link": str | None, "event_uri": str | None, "error": str | None}.

    Note: phone/topic aren't accepted by Calendly's invitee schema directly — phone isn't a
    standard invitee field, and topic has no dedicated slot either. Both get logged to our
    own Supabase audit trail (booking_db.log_meeting) instead; Calendly itself only gets
    name + email, which is all its API supports here without custom questions configured
    """
    api_key = os.getenv("CALENDLY_API_KEY", "")
    if not api_key:
        return {"success": False, "meet_link": None, "event_uri": None, "error": "calendly_not_configured"}

    event_type_uri = _resolve_event_type(meeting_type)
    if not event_type_uri:
        return {"success": False, "meet_link": None, "event_uri": None, "error": "calendly_not_configured"}

    payload = {
        "event_type": event_type_uri,
        "start_time": start_time,
        "invitee": {
            "name": customer_name,
            "email": email,
            "timezone": TIMEZONE,
        },
    }

    try:
        async with aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT) as session:
            async with session.post(f"{_API_BASE}/invitees", json=payload, headers=_headers()) as resp:
                if resp.status == 403:
                    logger.error(
                        "Calendly returned 403 on booking creation — this endpoint requires "
                        "Standard plan or above, Free plan is explicitly blocked here."
                    )
                    return {"success": False, "meet_link": None, "event_uri": None, "error": "plan_upgrade_required"}
                if resp.status >= 400:
                    body = await resp.text()
                    logger.error(f"Calendly booking failed ({resp.status}): {body}")
                    return {"success": False, "meet_link": None, "event_uri": None, "error": "booking_failed"}
                data = await resp.json()
    except Exception as e:
        logger.error(f"Calendly booking request failed: {e}")
        return {"success": False, "meet_link": None, "event_uri": None, "error": "calendly_unreachable"}

    resource = data.get("resource", {})
    event_uri = resource.get("event")

    meet_link = await _get_meet_link(event_uri) if event_uri else None

    logger.info(f"Booked {meeting_type} meeting via Calendly: {customer_name} at {start_time}")
    return {"success": True, "meet_link": meet_link, "event_uri": event_uri, "error": None}


async def _get_meet_link(event_uri: str) -> str | None:
    """Fetches the video conferencing join_url from the Scheduled Event resource."""
    try:
        async with aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT) as session:
            async with session.get(event_uri, headers=_headers()) as resp:
                if resp.status >= 400:
                    logger.warning(f"Could not fetch event details for Meet link ({resp.status})")
                    return None
                data = await resp.json()
    except Exception as e:
        logger.warning(f"Event detail request failed: {e}")
        return None

    location = data.get("resource", {}).get("location", {})
    # join_url is Calendly's documented field for video-conferencing location types
    # (google_conference, zoom, etc.) — if your event type uses a different location kind,
    # check what Calendly actually returns here and adjust.
    return location.get("join_url")
