"""
Function-calling tools for HackWhisper: AI Hacktoberfest Voice Calling Agent.

Pipecat auto-generates tool schemas from function names, type hints, and docstrings.
Tools exposed to the LLM:
1. search_events(city) -> city-wise and regional event discovery
2. get_event_details(event_id) -> detailed event info (date, time, venue, format)
3. get_registration_link(event_id) -> registration & website URLs
4. send_event_email(email, event_id) -> sends event details & link via Resend
6. transfer_to_human(reason) -> live call bridging to a human specialist
7. end_call(farewell) -> polite termination
"""

import asyncio
import os
import re
from loguru import logger
from typing import Any

try:
    from pipecat.frames.frames import EndWorkerFrame, TTSSpeakFrame
    from pipecat.processors.frame_processor import FrameDirection
    from pipecat.services.llm_service import FunctionCallParams
except ImportError:
    EndWorkerFrame = None
    TTSSpeakFrame = None
    FrameDirection = None
    FunctionCallParams = Any
import aiohttp

from demo_display import DemoDisplay
import email_sender
import event_manager
import sms_sender


def _normalize_email(raw: str) -> str:
    """Convert spoken email transcriptions to clean, valid addresses.
    Handles forms like:
      'junaid dot khan at gmail dot com' -> 'junaid.khan@gmail.com'
      'j-u-n-a-i-d dot k zero triple three at gmail dot com' -> 'junaid.k0333@gmail.com'
    """
    if not raw:
        return ""
    s = str(raw).lower().strip()
    s = s.strip("<>()[]\"'` \t\r\n.,;:")

    # Spoken syntax normalization
    s = re.sub(r"[\(\[\{]\s*(?:dot|point|\.)\s*[\)\]\}]", ".", s)
    s = re.sub(r"[\(\[\{]\s*(?:at|@)\s*[\)\]\}]", "@", s)
    s = re.sub(r"\b(?:dot|point)\b", ".", s)
    s = re.sub(r"\b(?:at the rate of|at the rate|at sign|at)\b", "@", s)
    s = re.sub(r"\b(?:underscore)\b", "_", s)
    s = re.sub(r"\b(?:dash|hyphen)\b", "-", s)

    # Provider audio fixes
    s = re.sub(r"@\s*(?:g\s*mail|gee\s*mail)\b", "@gmail", s)
    s = re.sub(r"@\s*(?:out\s*look)\b", "@outlook", s)
    s = re.sub(r"@\s*(?:hot\s*mail)\b", "@hotmail", s)
    s = re.sub(r"@\s*(?:i\s*cloud|eye\s*cloud)\b", "@icloud", s)
    s = re.sub(r"@\s*(?:ya\s*hoo)\b", "@yahoo", s)

    _word_to_digit = {
        "zero": "0", "oh": "0", "o": "0",
        "one": "1", "two": "2", "to": "2", "too": "2",
        "three": "3", "tree": "3", "t": "3",
        "four": "4", "for": "4", "five": "5",
        "six": "6", "seven": "7", "eight": "8", "ate": "8", "nine": "9",
    }

    def _rep_triple(m):
        w = m.group(1).lower()
        d = _word_to_digit.get(w, w if w.isdigit() else "")
        return (d * 3) if d else m.group(0)

    s = re.sub(r"\btriple\s+([a-z0-9]+)\b", _rep_triple, s)

    def _rep_double(m):
        w = m.group(1).lower()
        d = _word_to_digit.get(w, w if w.isdigit() else "")
        return (d * 2) if d else m.group(0)

    s = re.sub(r"\bdouble\s+([a-z0-9]+)\b", _rep_double, s)

    for w, d in [
        ("zero", "0"), ("one", "1"), ("two", "2"), ("three", "3"),
        ("four", "4"), ("five", "5"), ("six", "6"), ("seven", "7"),
        ("eight", "8"), ("nine", "9")
    ]:
        s = re.sub(rf"\b{w}\b", d, s)

    # Collapse spelled-out letters
    for _ in range(12):
        s = re.sub(r"(?<=\b[a-z0-9])[- ]+(?=[a-z0-9]\b)", "", s)

    s = re.sub(r"\s+", "", s)
    s = re.sub(r"\.+@", "@", s)
    s = re.sub(r"@\.+", "@", s)
    return s.strip(".")


async def search_events(params: FunctionCallParams, city: str):
    """Search for Hacktoberfest events taking place in a specific city, region, or topic.
    Always call this when the caller asks what events are happening in their city or area.

    Args:
        city: The name of the city, state, or region (e.g. 'Kolkata', 'London', 'Berlin', 'New York').
    """
    DemoDisplay.tool_action(f"Searching Hacktoberfest events in {city}...")
    results = await event_manager.search_events_by_city(city, limit=4)

    if not results:
        DemoDisplay.tool_result(f"No specific events found in {city}.")
        await params.result_callback({
            "success": True,
            "found": False,
            "city": city,
            "message": f"No local events found specifically for '{city}'. Many Hacktoberfest events are virtual and accessible worldwide on hacktoberfest.com.",
            "events": [],
        })
        return

    DemoDisplay.tool_result(f"Found {len(results)} events in {city}")
    formatted = []
    for ev in results:
        formatted.append({
            "event_id": ev["id"],
            "name": ev["name"],
            "city": ev["city"],
            "country": ev["country"],
            "status": ev["status"],
            "date_time": ev["starts_at_str"],
            "format": ev["format"],
        })

    await params.result_callback({
        "success": True,
        "found": True,
        "city": city,
        "count": len(formatted),
        "events": formatted,
    })


async def get_event_details(params: FunctionCallParams, event_id: str):
    """Retrieve in-depth details for a specific Hacktoberfest event, including date, time,
    venue address, format, and overview.

    Args:
        event_id: The event ID or slug returned from search_events.
    """
    DemoDisplay.tool_action(f"Fetching details for event {event_id[:16]}...")
    ev = await event_manager.get_event_by_id_or_name(event_id)

    if not ev:
        DemoDisplay.tool_result("Event details not found")
        await params.result_callback({
            "success": False,
            "error": "event_not_found",
            "message": f"Could not find details for event ID '{event_id}'.",
        })
        return

    DemoDisplay.tool_result(f"Fetched details for {ev['name'][:30]}")
    await params.result_callback({
        "success": True,
        "event_id": ev["id"],
        "name": ev["name"],
        "city": ev.get("city", "TBA"),
        "venue_address": ev.get("address") or ev.get("city") or "Online / In-person",
        "starts_at": ev.get("starts_at_str", "TBA"),
        "ends_at": ev.get("ends_at_str", "TBA"),
        "format": ev.get("format", "Hack Day"),
        "status": ev.get("status", "Active"),
        "description_summary": (ev.get("description") or "")[:280],
        "registration_url": ev.get("registration_url") or ev.get("website_url"),
    })


async def get_registration_link(params: FunctionCallParams, event_id: str):
    """Get the official registration link and web link for a Hacktoberfest event.
    Use this when the caller asks how to register or asks for the link. You can offer
    to send it to their email address.

    Args:
        event_id: The event ID or slug.
    """
    DemoDisplay.tool_action(f"Looking up registration URL for {event_id[:16]}...")
    ev = await event_manager.get_event_by_id_or_name(event_id)

    if not ev:
        await params.result_callback({"success": False, "error": "event_not_found"})
        return

    reg_url = ev.get("registration_url") or ev.get("website_url") or "https://hacktoberfest.com/fests"
    DemoDisplay.tool_result(f"Registration URL: {reg_url[:40]}...")

    await params.result_callback({
        "success": True,
        "event_id": ev["id"],
        "event_name": ev["name"],
        "registration_url": reg_url,
        "website_url": ev.get("website_url"),
        "can_send_via": ["email"],
    })


async def send_event_email(
    params: FunctionCallParams,
    email: str,
    event_id: str,
    attendee_name: str = "Hacker",
):
    """Send Hacktoberfest event details and registration link directly to the caller's email.
    Always repeat the email address back to confirm before calling this tool.

    Args:
        email: The attendee's email address.
        event_id: The ID of the event to send.
        attendee_name: Caller's name (optional).
    """
    app_resources = params.app_resources or {}
    clean_email = _normalize_email(email)
    call_id = app_resources.get("session_id")

    DemoDisplay.tool_action(f"Sending event email to {clean_email} for event {event_id[:16]}...")

    ev = await event_manager.get_event_by_id_or_name(event_id)
    if not ev:
        DemoDisplay.tool_result("Email send failed: event not found")
        await params.result_callback({"success": False, "error": "event_not_found"})
        return

    result = await email_sender.send_event_email(
        to=clean_email,
        customer_name=attendee_name,
        event_name=ev["name"],
        starts_at_str=ev.get("starts_at_str", "See event page"),
        city=ev.get("city", ""),
        venue=ev.get("address", ""),
        description=ev.get("description", ""),
        registration_url=ev.get("registration_url") or ev.get("website_url", ""),
        website_url=ev.get("website_url"),
    )

    call_state = app_resources.get("call_state")
    if isinstance(call_state, dict):
        call_state["outcome"] = "email_sent"

    await event_manager.log_notification(
        event_id=ev["id"],
        recipient=clean_email,
        channel="email",
        message=f"Event details for {ev['name']}",
        call_id=call_id,
        status="sent" if result["success"] else "failed",
    )

    if result["success"]:
        DemoDisplay.tool_result(f"Event email dispatched to {clean_email}!")
    else:
        DemoDisplay.tool_result(f"Email error: {result.get('error')}")

    await params.result_callback({
        "success": result["success"],
        "email": clean_email,
        "event_name": ev["name"],
        "error": result.get("error"),
    })


async def send_event_sms(
    params: FunctionCallParams,
    event_id: str,
    phone_number: str = "",
):
    """Send Hacktoberfest event details and registration link directly via SMS text message.
    If phone_number is not provided by the caller, the system automatically uses their caller ID.

    Args:
        event_id: The ID of the event.
        phone_number: Destination phone number (leave blank if sending to the current caller ID).
    """
    app_resources = params.app_resources or {}
    call_id = app_resources.get("session_id")
    target_phone = phone_number or app_resources.get("caller_phone") or ""

    if not target_phone:
        await params.result_callback({
            "success": False,
            "error": "missing_phone_number",
            "message": "I don't have a phone number to text. Could you please tell me your mobile number?",
        })
        return

    DemoDisplay.tool_action(f"Sending event SMS to {target_phone} for event {event_id[:16]}...")
    ev = await event_manager.get_event_by_id_or_name(event_id)

    if not ev:
        DemoDisplay.tool_result("SMS failed: event not found")
        await params.result_callback({"success": False, "error": "event_not_found"})
        return

    result = await sms_sender.send_event_sms(
        to_phone=target_phone,
        event_name=ev["name"],
        starts_at_str=ev.get("starts_at_str", "Check registration link"),
        city=ev.get("city", ""),
        registration_url=ev.get("registration_url") or ev.get("website_url", ""),
        website_url=ev.get("website_url"),
    )

    call_state = app_resources.get("call_state")
    if isinstance(call_state, dict):
        call_state["outcome"] = "sms_sent"

    await event_manager.log_notification(
        event_id=ev["id"],
        recipient=target_phone,
        channel="sms",
        message=result.get("message", f"SMS for {ev['name']}"),
        call_id=call_id,
        status="sent" if result["success"] else "failed",
    )

    if result["success"]:
        DemoDisplay.tool_result(f"Event SMS sent to {target_phone}!")
    else:
        DemoDisplay.tool_result(f"SMS error: {result.get('error')}")

    await params.result_callback({
        "success": result["success"],
        "phone": target_phone,
        "event_name": ev["name"],
        "error": result.get("error"),
    })


async def transfer_to_human(params: FunctionCallParams, reason: str):
    """Transfer the caller to a live human organizer or support specialist right now.
    Use when the caller explicitly asks to speak to an organizer or human agent.

    Args:
        reason: Short reason human transfer was requested.
    """
    app_resources = params.app_resources or {}
    call_state = app_resources.get("call_state")
    if isinstance(call_state, dict):
        call_state["outcome"] = "transferred"

    transfer_phone = os.getenv("TRANSFER_PHONE_NUMBER", "").strip()
    telnyx_api_key = os.getenv("TELNYX_API_KEY", "").strip()
    call_control_id = app_resources.get("call_control_id")
    called_phone = app_resources.get("called_phone") or os.getenv("TELNYX_PHONE_NUMBER", "").strip()

    DemoDisplay.tool_action(f"Live transfer requested (Reason: {reason})")

    if not transfer_phone or not telnyx_api_key or not call_control_id:
        DemoDisplay.tool_result("Transfer not configured")
        await params.result_callback({
            "success": False,
            "transferred": False,
            "error": "transfer_not_configured",
        })
        return

    announcement = "Please hold on for just a moment while I connect you with an event organizer."
    DemoDisplay.bot_said(announcement)
    await params.llm.push_frame(TTSSpeakFrame(announcement))
    await asyncio.sleep(2.5)

    transfer_url = f"https://api.telnyx.com/v2/calls/{call_control_id}/actions/transfer"
    payload = {"to": transfer_phone}
    if called_phone:
        payload["from"] = called_phone

    headers = {
        "Authorization": f"Bearer {telnyx_api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
            async with session.post(transfer_url, json=payload, headers=headers) as resp:
                if resp.status in (200, 202):
                    DemoDisplay.tool_result(f"Successfully transferred to {transfer_phone}")
                    await params.result_callback({
                        "success": True,
                        "transferred": True,
                        "transferred_to": transfer_phone,
                    })
                    await params.llm.push_frame(EndWorkerFrame(), FrameDirection.UPSTREAM)
                    return
                else:
                    DemoDisplay.tool_result(f"Transfer failed: HTTP {resp.status}")
                    await params.result_callback({"success": False, "transferred": False})
    except Exception as e:
        logger.error(f"Transfer exception: {e}")
        await params.result_callback({"success": False, "transferred": False})


async def end_call(params: FunctionCallParams, farewell: str):
    """End the call politely once all questions are answered or caller is ready to hang up.

    Args:
        farewell: The complete, spoken goodbye line (1-2 sentences).
    """
    DemoDisplay.tool_action("Ending call with farewell...")
    DemoDisplay.bot_said(farewell)
    await params.result_callback({"success": True})
    await params.llm.push_frame(TTSSpeakFrame(farewell))
    await params.llm.push_frame(EndWorkerFrame(), FrameDirection.UPSTREAM)


HACKTOBERFEST_TOOLS = [
    search_events,
    get_event_details,
    get_registration_link,
    send_event_email,
    transfer_to_human,
    end_call,
]

# Aliases for backwards compatibility
INNOVENTIX_TOOLS = HACKTOBERFEST_TOOLS
