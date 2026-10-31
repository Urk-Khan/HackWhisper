"""
Cal.com v2 integration — replaces calendly.py. Same plain-aiohttp pattern as the rest of
this project, no SDK. Exposes the same three functions calendly.py did (get_open_slots,
book_slot, format_display) with the same argument/return shapes, so tools.py only needs its
import changed, not its logic.

Why the switch from Calendly: Calendly's POST /invitees (booking creation) returns 403 on
their Free plan — Standard plan or above is required. Cal.com's booking-creation endpoint
has no equivalent plan restriction, so this unblocks testing/production on a free Cal.com
account.

Auth: a Cal.com API key (Settings -> Security -> API Keys in the Cal.com dashboard). Test-mode
keys start with `cal_`, live-mode keys with `cal_live_`. Like Calendly's PAT, this doesn't
auto-refresh — it's a fixed Bearer token from .env.

One-time setup:
  1. Create your Event Type in Cal.com's UI (this project uses ONE event type for both Sales
     and Support, same as the current Calendly setup — see _resolve_event_type below if you
     later want to split them).
  2. Enable a video-conferencing location (Google Meet or Zoom) in that event type's location
     settings, the same way you would have for Calendly.
  3. Copy the event's PUBLIC BOOKING URL (e.g. https://cal.com/your-username/your-event-slug)
     into .env as CAL_BOOKING_URL. This module parses the username and event slug directly
     out of that URL — no need to separately look up a numeric event type ID.

API notes (verified against Cal.com's own v2 docs, not guessed):
  - GET /v2/slots requires a `cal-api-version: 2024-09-04` header. Response groups slots by
    date: {"data": {"2026-09-15": [{"start": "..."}], "2026-09-16": [...]}} — this is a
    different shape than Calendly's flat list, so get_open_slots flattens it here.
  - POST /v2/bookings requires a DIFFERENT pinned version header: `cal-api-version:
    2024-08-13`. Body shape: {start, attendee: {name, email, timeZone, phoneNumber,
    language}, eventTypeSlug, username}. No plan-tier restriction on this endpoint.
  - The created booking's video link can show up in a few different places depending on
    which conferencing integration is configured: top-level `meetingUrl`, nested
    `metadata.videoCallUrl`, or occasionally `location` itself. _extract_meet_link checks
    all three so this doesn't silently break if your event type's video provider changes.

Design note (same anti-reformatting principle as calendly.py): slots are identified by the
exact ISO `start` string Cal.com returns. tools.py must pass this back to book_slot EXACTLY
as received — never reconstruct or reformat it.
"""

import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import aiohttp
from loguru import logger

CAL_API_KEY = os.getenv("CAL_API_KEY", "")
LOOKAHEAD_DAYS = int(os.getenv("CAL_LOOKAHEAD_DAYS", "14"))

_API_BASE = "https://api.cal.com/v2"
_SLOTS_API_VERSION = "2024-09-04"
_BOOKINGS_API_VERSION = "2024-08-13"
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)


def _parse_booking_url(url: str) -> tuple[str, str]:
    """Extracts (username, event_slug) from a Cal.com public booking URL, e.g.
    'https://cal.com/ubaid-persistbrands/free-consultation' -> ('ubaid-persistbrands',
    'free-consultation'). Returns ('', '') if the URL doesn't parse into exactly two path
    segments (also handles a stray trailing slash)."""
    if not url:
        return "", ""
    path = urlparse(url).path.strip("/")
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        logger.error(f"CAL_BOOKING_URL doesn't look like a Cal.com event booking URL: {url!r}")
        return "", ""
    # Cal.com team-event URLs can have an extra segment (team/org slug) before the event
    # slug — the event slug is always the LAST segment, the username/team is the first.
    return parts[0], parts[-1]


def _resolve_event_type(meeting_type: str) -> tuple[str, str]:
    """Returns (username, event_slug) for the given meeting_type. Currently one Cal.com
    event type is used for both 'sales' and 'support' (mirrors the current Calendly setup).
    If you later create a separate event type per meeting type, add a second env var here
    (e.g. CAL_SUPPORT_BOOKING_URL) and branch on meeting_type.lower() like calendly.py did."""
    booking_url = os.getenv("CAL_BOOKING_URL", "")
    return _parse_booking_url(booking_url)


def _headers(api_version: str) -> dict:
    api_key = os.getenv("CAL_API_KEY", "")
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "cal-api-version": api_version,
    }


def _get_timezone() -> str:
    """Read TIMEZONE fresh from environment (defaults to Asia/Karachi)."""
    return os.getenv("TIMEZONE", "Asia/Karachi")


def format_display(start_time_iso: str) -> tuple[str, str]:
    """ISO datetime -> (date, time) display strings in the configured TIMEZONE, for the LLM
    to read aloud. Purely cosmetic — book_slot keys off the raw start string, not these
    strings. Reads TIMEZONE fresh (not the module-level constant) so it is always accurate."""
    from zoneinfo import ZoneInfo

    tz_name = _get_timezone()
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")

    dt = datetime.fromisoformat(start_time_iso.replace("Z", "+00:00")).astimezone(tz)
    date_str = dt.strftime("%A, %b %d")
    time_str = dt.strftime("%I:%M %p").lstrip("0") or dt.strftime("%I:%M %p")
    return date_str, time_str


def _extract_meet_link(booking: dict) -> str | None:
    """Cal.com's video link location varies by which conferencing integration is configured
    on the event type — check the known spots in order of how commonly they're populated."""
    meeting_url = booking.get("meetingUrl")
    if meeting_url and meeting_url.startswith("http"):
        return meeting_url
    metadata_url = (booking.get("metadata") or {}).get("videoCallUrl")
    if metadata_url:
        return metadata_url
    location = booking.get("location")
    if isinstance(location, str) and location.startswith("http"):
        return location
    return None


async def get_open_slots(meeting_type: str, limit: int = 20) -> list[dict]:
    """Returns open slots distributed across the available days of the upcoming week:
    [{"start_time": "<exact ISO string>", "weekday": "...", "date": "...", "time": "..."}, ...].
    "start_time" is what must be passed back to book_slot verbatim.
    Slots are selected evenly across up to 5 available business days so the voice agent
    has coverage for the entire week (morning, midday, afternoon) rather than bunching
    all slots into the first day."""
    api_key = os.getenv("CAL_API_KEY", "")
    lookahead_days = int(os.getenv("CAL_LOOKAHEAD_DAYS", "14"))
    tz_name = _get_timezone()

    if not api_key:
        logger.error("CAL_API_KEY not set in .env")
        return []

    username, event_slug = _resolve_event_type(meeting_type)
    if not username or not event_slug:
        logger.error(f"No Cal.com event configured (check CAL_BOOKING_URL) for meeting_type={meeting_type!r}")
        return []

    now = datetime.now(timezone.utc)
    start = (now + timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
    end = (now + timedelta(days=lookahead_days)).isoformat().replace("+00:00", "Z")

    url = f"{_API_BASE}/slots"
    params = {
        "eventTypeSlug": event_slug,
        "username": username,
        "start": start,
        "end": end,
        "timeZone": tz_name,
    }

    try:
        async with aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT) as session:
            async with session.get(url, params=params, headers=_headers(_SLOTS_API_VERSION)) as resp:
                if resp.status >= 400:
                    logger.error(f"Cal.com availability check failed ({resp.status}): {await resp.text()}")
                    return []
                data = await resp.json()
    except Exception as e:
        logger.error(f"Cal.com availability request failed: {e}")
        return []

    by_date = data.get("data", {})
    from zoneinfo import ZoneInfo
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")

    # Group valid slots by date
    days_map: dict[str, list[dict]] = {}
    for date_key in sorted(by_date.keys()):
        raw_items = by_date[date_key]
        if not raw_items:
            continue
        for item in raw_items:
            start_iso = item.get("start")
            if not start_iso:
                continue
            dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00")).astimezone(tz)
            day_str = dt.strftime("%A, %b %d")
            weekday = dt.strftime("%A")
            time_str = dt.strftime("%I:%M %p").lstrip("0") or dt.strftime("%I:%M %p")
            days_map.setdefault(day_str, []).append({
                "start_time": start_iso,
                "weekday": weekday,
                "date": day_str,
                "time": time_str,
            })

    if not days_map:
        return []

    # Distribute slots across up to 5 available days so the entire week is represented
    slots: list[dict] = []
    max_days = min(len(days_map), 5)
    slots_per_day = max(2, limit // max_days) if max_days > 0 else 3

    for day_str, day_slots in list(days_map.items())[:max_days]:
        if len(day_slots) <= slots_per_day:
            slots.extend(day_slots)
        else:
            # Pick evenly spaced slots across the day (morning, midday, afternoon)
            step = len(day_slots) / slots_per_day
            indices = sorted(list(set(int(i * step) for i in range(slots_per_day))))
            slots.extend([day_slots[i] for i in indices])

        if len(slots) >= limit:
            break

    return slots[:limit] if limit and len(slots) > limit else slots


async def _find_existing_booking(session: aiohttp.ClientSession, email: str) -> dict | None:
    """Helper to recover from HTTP 409 Conflict when a booking was already created in Cal.com
    (e.g., from an interrupted LLM tool call turn)."""
    try:
        async with session.get(
            f"{_API_BASE}/bookings", headers=_headers(_BOOKINGS_API_VERSION)
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                bookings = data.get("data", [])
                for b in bookings:
                    for att in b.get("attendees", []):
                        if att.get("email", "").lower() == email.lower():
                            return b
    except Exception as e:
        logger.warning(f"Failed to query existing Cal.com bookings: {e}")
    return None


async def book_slot(
    *, meeting_type: str, start_time: str, customer_name: str, phone: str, email: str, topic: str
) -> dict:
    """Books the slot at the given start_time (must be the exact ISO string from
    get_open_slots). Returns
    {"success": bool, "meet_link": str | None, "event_uri": str | None, "error": str | None}.

    Note: `topic` isn't sent to Cal.com — like calendly.py, it's logged to our own Supabase
    audit trail (booking_db.log_meeting) instead, since it has no dedicated field here
    without custom booking-form questions configured on the event type."""
    api_key = os.getenv("CAL_API_KEY", "")
    if not api_key:
        return {"success": False, "meet_link": None, "event_uri": None, "error": "cal_not_configured"}

    username, event_slug = _resolve_event_type(meeting_type)
    if not username or not event_slug:
        return {"success": False, "meet_link": None, "event_uri": None, "error": "cal_not_configured"}

    attendee = {
        "name": customer_name,
        "email": email,
        "timeZone": os.getenv("TIMEZONE", "UTC"),
        "language": "en",
    }
    if phone:
        attendee["phoneNumber"] = phone

    payload = {
        "start": start_time,
        "attendee": attendee,
        "eventTypeSlug": event_slug,
        "username": username,
    }

    try:
        async with aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT) as session:
            async with session.post(
                f"{_API_BASE}/bookings", json=payload, headers=_headers(_BOOKINGS_API_VERSION)
            ) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    logger.error(f"Cal.com booking failed ({resp.status}): {body}")
                    if resp.status == 409:
                        existing = await _find_existing_booking(session, email)
                        if existing:
                            meet_link = _extract_meet_link(existing)
                            event_uid = existing.get("uid")
                            logger.info(f"Resolved 409 Conflict: existing booking found for {email} (uid={event_uid})")
                            return {"success": True, "meet_link": meet_link, "event_uri": event_uid, "error": None}
                    return {"success": False, "meet_link": None, "event_uri": None, "error": "booking_failed"}
                data = await resp.json()
    except Exception as e:
        logger.error(f"Cal.com booking request failed: {e}")
        return {"success": False, "meet_link": None, "event_uri": None, "error": "cal_unreachable"}

    booking = data.get("data", {})
    meet_link = _extract_meet_link(booking)
    event_uid = booking.get("uid")

    logger.info(f"Booked {meeting_type} meeting via Cal.com: {customer_name} at {start_time}")
    return {"success": True, "meet_link": meet_link, "event_uri": event_uid, "error": None}
