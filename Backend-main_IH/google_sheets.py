"""
⚠️  CURRENTLY INACTIVE — replaced by cal_com.py

This file is preserved so you can re-enable the Google Sheets + Google Calendar
flow once you have a Google Cloud service account. To switch back:
  1. Restore the commented-out env vars in .env (GOOGLE_SHEETS_* and GOOGLE_CALENDAR_*).
  2. In tools.py: comment out `import cal_com`, uncomment `import google_sheets`,
     `import google_calendar`, `from email_sender import send_meeting_confirmation`,
     and restore the original check_availability / book_meeting bodies.
  3. Place your service-account.json in the project root.

"""
"""
Google Sheets integration — a "Slots" tab (one per meeting type — Sales and Support can
have different reps/availability) defines bookable meeting times. get_open_slots() reads
open slots and cross-checks them against Google Calendar (google_calendar.py) so a slot
that's marked Open in the Sheet but actually blocked on the rep's real calendar (PTO, a
call booked outside this system) doesn't get offered. book_slot() marks a Sheet row taken.

Auth: a Google Cloud service account (JSON key file). Create one in Google Cloud Console
(APIs & Services -> Credentials -> Create Service Account -> add a JSON key), enable the
Google Sheets API for that project, then share your spreadsheet with the service account's
email address (found in the JSON file, looks like `xxx@your-project.iam.gserviceaccount.com`)
as an Editor — writes will fail with a 403 otherwise.

Sheet schema expected on EACH meeting-type tab (row 1 = headers, data starts row 2):
  A: Date        e.g. "2026-09-15" — must match google_calendar.py's expected format
  B: Time        e.g. "10:00 AM"   — must match google_calendar.py's expected format
  C: Status      "Open" or "Booked"
  D: Booked By   filled in when booked
  E: Phone
  F: Email
  G: Topic
  H: Booked At   timestamp, filled in when booked

Pre-fill rows with your open slots (Status="Open") ahead of time — the bot only offers what's
already there, it doesn't generate new slots itself.
"""

import asyncio
import os
import time
from datetime import datetime, timezone

import aiohttp
from loguru import logger

from google_calendar import is_slot_free, resolve_calendar_id

SPREADSHEET_ID = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "")
CREDENTIALS_PATH = os.getenv("GOOGLE_SHEETS_CREDENTIALS_PATH", "")

# Two separate tabs by default (Sales and Support commonly have different reps/availability).
# Point both env vars at the same tab name if it's one small team sharing a single tab.
SLOTS_TABS = {
    "sales": os.getenv("GOOGLE_SHEETS_SALES_TAB", "Sales_Slots"),
    "support": os.getenv("GOOGLE_SHEETS_SUPPORT_TAB", "Support_Slots"),
}

_SHEETS_API_BASE = "https://sheets.googleapis.com/v4/spreadsheets"
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=8)

_cached_token: str | None = None
_cached_token_expiry: float = 0.0
_token_lock = asyncio.Lock()


def _resolve_tab(meeting_type: str) -> str:
    return SLOTS_TABS.get(meeting_type.lower(), SLOTS_TABS["sales"])


def _refresh_token_sync() -> tuple[str, float]:
    """Blocking (network + crypto) — always run via asyncio.to_thread. Returns
    (access_token, expiry_unix_ts)."""
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(
        CREDENTIALS_PATH, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    creds.refresh(Request())
    return creds.token, creds.expiry.replace(tzinfo=timezone.utc).timestamp()


async def _get_access_token() -> str | None:
    """Tokens last ~1hr; cached and refreshed a minute before expiry so most calls skip the
    blocking refresh entirely."""
    global _cached_token, _cached_token_expiry

    if not CREDENTIALS_PATH:
        logger.error("GOOGLE_SHEETS_CREDENTIALS_PATH not set in .env")
        return None
    if not os.path.exists(CREDENTIALS_PATH):
        logger.error(f"GOOGLE_SHEETS_CREDENTIALS_PATH points to a file that doesn't exist: {CREDENTIALS_PATH}")
        return None

    async with _token_lock:
        now = time.time()
        if _cached_token and now < _cached_token_expiry - 60:
            return _cached_token
        try:
            token, expiry = await asyncio.to_thread(_refresh_token_sync)
        except Exception as e:
            logger.error(f"Failed to get Google Sheets access token: {e}")
            return None
        _cached_token, _cached_token_expiry = token, expiry
        return token


async def _read_sheet_open_slots(meeting_type: str, limit: int) -> list[dict]:
    """Sheet-only pass — doesn't check the Calendar yet (that's get_open_slots below)."""
    token = await _get_access_token()
    if not token or not SPREADSHEET_ID:
        return []

    tab = _resolve_tab(meeting_type)
    url = f"{_SHEETS_API_BASE}/{SPREADSHEET_ID}/values/{tab}!A2:H"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        async with aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status >= 400:
                    logger.error(f"Sheets read failed ({resp.status}): {await resp.text()}")
                    return []
                data = await resp.json()
    except Exception as e:
        logger.error(f"Sheets read request failed: {e}")
        return []

    rows = data.get("values", [])
    open_slots = []
    for i, row in enumerate(rows):
        date = row[0] if len(row) > 0 else ""
        time_str = row[1] if len(row) > 1 else ""
        status = row[2] if len(row) > 2 else ""
        if status.strip().lower() == "open" and date and time_str:
            open_slots.append({"date": date, "time": time_str, "_row": i + 2})  # +2: header + 1-index
        if len(open_slots) >= limit:
            break

    return open_slots


async def get_open_slots(meeting_type: str, limit: int = 15) -> list[dict]:
    """Sheet slots marked Open, intersected with what's actually free on the corresponding
    Calendar. Returns up to `limit`: [{"date": ..., "time": ..., "_row": <sheet row #>}, ...].
    "_row" is internal bookkeeping for book_slot() — never surface it to the caller.

    Checks Sheet candidates against the Calendar concurrently (not one at a time) — a serial
    freeBusy call per candidate would add real latency while the caller is on hold.
    """
    sheet_candidates = await _read_sheet_open_slots(meeting_type, limit=limit * 3)
    if not sheet_candidates:
        return []

    calendar_id = resolve_calendar_id(meeting_type)
    if not calendar_id:
        logger.warning(f"No calendar configured for meeting_type={meeting_type!r} — skipping Calendar cross-check")
        return sheet_candidates[:limit]

    free_flags = await asyncio.gather(
        *[
            is_slot_free(calendar_id=calendar_id, date=s["date"], time_str=s["time"])
            for s in sheet_candidates
        ]
    )

    confirmed = [s for s, is_free in zip(sheet_candidates, free_flags) if is_free]
    return confirmed[:limit]


async def book_slot(
    *, meeting_type: str, date: str, time_str: str, customer_name: str, phone: str, email: str, topic: str
) -> dict:
    """Finds the open slot matching date+time EXACTLY as returned by get_open_slots (the LLM
    is instructed to pass these back verbatim, not reformat them) and marks it booked in the
    Sheet. Re-fetches fresh slots first to minimize the double-booking race window. This does
    NOT create the Calendar event — that's a separate step in tools.py (create_meeting_event
    in google_calendar.py), called only after this Sheet write succeeds. Returns
    {"success": bool, "error": str | None}."""
    token = await _get_access_token()
    if not token or not SPREADSHEET_ID:
        return {"success": False, "error": "sheets_not_configured"}

    slots = await get_open_slots(meeting_type, limit=500)
    match = next((s for s in slots if s["date"] == date and s["time"] == time_str), None)
    if not match:
        return {"success": False, "error": "slot_no_longer_available"}

    tab = _resolve_tab(meeting_type)
    row_number = match["_row"]
    booked_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    range_ = f"{tab}!C{row_number}:H{row_number}"
    url = f"{_SHEETS_API_BASE}/{SPREADSHEET_ID}/values/{range_}?valueInputOption=USER_ENTERED"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"values": [["Booked", customer_name, phone, email, topic, booked_at]]}

    try:
        async with aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT) as session:
            async with session.put(url, json=payload, headers=headers) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    logger.error(f"Sheets write failed ({resp.status}): {body}")
                    return {"success": False, "error": "sheets_write_failed"}
    except Exception as e:
        logger.error(f"Sheets write request failed: {e}")
        return {"success": False, "error": "sheets_unreachable"}

    logger.info(f"Booked {meeting_type} meeting: {customer_name} on {date} at {time_str}")
    return {"success": True, "error": None}


async def release_slot(*, meeting_type: str, date: str, time_str: str) -> None:
    """Reverts a slot back to Open and clears its booking fields. Used to roll back a
    successful Sheet write when a LATER step (Calendar event creation) fails — otherwise
    the slot would be stuck "Booked" with no real meeting behind it. Best-effort: logs a
    warning on failure rather than raising, since this is already a failure-recovery path
    and there's nothing further to roll back to."""
    token = await _get_access_token()
    if not token or not SPREADSHEET_ID:
        return

    tab = _resolve_tab(meeting_type)

    # Find the row currently booked for this exact date+time (mirrors book_slot's matching).
    url = f"{_SHEETS_API_BASE}/{SPREADSHEET_ID}/values/{tab}!A2:H"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status >= 400:
                    logger.warning(f"release_slot: Sheets read failed ({resp.status})")
                    return
                data = await resp.json()
    except Exception as e:
        logger.warning(f"release_slot: Sheets read request failed: {e}")
        return

    row_number = None
    for i, row in enumerate(data.get("values", [])):
        row_date = row[0] if len(row) > 0 else ""
        row_time = row[1] if len(row) > 1 else ""
        if row_date == date and row_time == time_str:
            row_number = i + 2
            break

    if row_number is None:
        logger.warning(f"release_slot: no matching row found for {date} {time_str} — nothing to revert")
        return

    range_ = f"{tab}!C{row_number}:H{row_number}"
    url = f"{_SHEETS_API_BASE}/{SPREADSHEET_ID}/values/{range_}?valueInputOption=USER_ENTERED"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"values": [["Open", "", "", "", "", ""]]}

    try:
        async with aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT) as session:
            async with session.put(url, json=payload, headers=headers) as resp:
                if resp.status >= 400:
                    logger.warning(f"release_slot: Sheets write failed ({resp.status}): {await resp.text()}")
                    return
    except Exception as e:
        logger.warning(f"release_slot: Sheets write request failed: {e}")
        return

    logger.info(f"Released slot back to Open: {date} {time_str} ({meeting_type})")
