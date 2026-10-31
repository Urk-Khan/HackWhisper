"""
Function-calling tools exposed to the LLM (Pipecat 1.8.1 API).

Pipecat auto-generates each tool's schema from its name, type hints, and docstring — list
these on the LLMContext: LLMContext(tools=INNOVENTIX_TOOLS).

Scheduling backend: Cal.com (cal_com.py) — replaces Calendly (calendly.py is now parked,
same as the earlier Google Sheets/Calendar attempt before it, and email_sender.py). Easy to
revert to either later without rebuilding from scratch, since all three keep the same
get_open_slots/book_slot/format_display interface.

No N8N anywhere — every tool talks to its backend directly via aiohttp.
"""

from loguru import logger
from pipecat.frames.frames import EndWorkerFrame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import FunctionCallParams

import asyncio
import os
import re

import aiohttp

import booking_db
import cal_com as scheduler
from demo_display import DemoDisplay
import email_sender


def _normalize_email(raw: str) -> str:
    """Convert spoken email transcriptions to clean, valid addresses.
    Handles forms like:
      'junaid dot khan at innoventix hub dot tech' -> 'junaid.khan@innoventixhub.tech'
      'j-u-n-a-i-d dot k-h-a-n at innoventixhub dot tech' -> 'junaid.khan@innoventixhub.tech'
      'alex underscore smith at acme dot co dot uk' -> 'alex_smith@acme.co.uk'
      'john dot doe at gmail dot com' -> 'john.doe@gmail.com'
      'junaid dot k zero triple three at gmail dot com' -> 'junaid.k0333@gmail.com'
    Strips accidental whitespace, brackets, spelled dashes, and fixes common telephony domain slips.
    """
    if not raw:
        return ""
    s = str(raw).lower().strip()
    # Strip enclosing punctuation or quotes
    s = s.strip("<>()[]\"'` \t\r\n.,;:")

    # Normalize spoken words and bracketed variations
    s = re.sub(r"[\(\[\{]\s*(?:dot|point|\.)\s*[\)\]\}]", ".", s)
    s = re.sub(r"[\(\[\{]\s*(?:at|@)\s*[\)\]\}]", "@", s)
    s = re.sub(r"\b(?:dot|point)\b", ".", s)
    s = re.sub(r"\b(?:at the rate of|at the rate|at sign|at)\b", "@", s)
    s = re.sub(r"\b(?:underscore)\b", "_", s)
    s = re.sub(r"\b(?:dash|hyphen)\b", "-", s)

    # Common speech-to-text slips for major global providers (Gmail, Outlook, Yahoo, Hotmail, iCloud)
    s = re.sub(r"@\s*(?:g\s*mail|gee\s*mail)\b", "@gmail", s)
    s = re.sub(r"@\s*(?:out\s*look)\b", "@outlook", s)
    s = re.sub(r"@\s*(?:hot\s*mail)\b", "@hotmail", s)
    s = re.sub(r"@\s*(?:i\s*cloud|eye\s*cloud)\b", "@icloud", s)
    s = re.sub(r"@\s*(?:ya\s*hoo)\b", "@yahoo", s)

    # Convert spoken numbers and multipliers in email usernames:
    # 'triple three' / 'triple t' -> '333', 'double zero' -> '00', 'zero' -> '0'
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

    # Collapse spelled-out letters with dashes or spaces: 'j-u-n-a-i-d' or 'j u n a i d' -> 'junaid'
    for _ in range(12):
        s = re.sub(r"(?<=\b[a-z0-9])[- ]+(?=[a-z0-9]\b)", "", s)

    # Remove all remaining whitespace
    s = re.sub(r"\s+", "", s)

    # Strip stray periods around @ or ends
    s = re.sub(r"\.+@", "@", s)
    s = re.sub(r"@\.+", "@", s)
    s = s.strip(".")

    # Common domain corrections for Innoventix Hub emails
    if "@innoventix" in s:
        s = re.sub(r"@innoventix(?:hub)?\.[a-z0-9.]+$", "@innoventixhub.tech", s)
        s = re.sub(r"@innoventix$", "@innoventixhub.tech", s)
        s = re.sub(r"@innoventixhub$", "@innoventixhub.tech", s)

    return s


async def check_availability(params: FunctionCallParams, meeting_type: str):
    """Look up currently open meeting slots across the upcoming week. ONLY call this once the
    caller has clearly agreed to schedule or book a call with the team.

    Do NOT call this speculatively while still just discussing services or answering questions.
    The results stay valid for the rest of this call, so re-use what you already got rather than
    re-checking.

    Returns open date/time options across the available days of the week, with a week summary.
    Present 2-3 open options across different days naturally, or focus on a specific day if the
    caller asked for it.

    Args:
        meeting_type: "sales" for anything involving buying a service, a cross-sell pitch,
            or general purchase interest. "support" only when the caller has an unresolved
            issue with a service they already have that you (the AI) genuinely could not
            answer from your own knowledge.
    """
    DemoDisplay.tool_action(f"Checking calendar availability for {meeting_type} meeting...")
    slots = await scheduler.get_open_slots(meeting_type, limit=20)

    if not slots:
        DemoDisplay.tool_result("No open slots found in the upcoming schedule.")
        await params.result_callback({
            "success": True,
            "slots": [],
            "available_days": [],
            "week_summary": "No open slots found in the upcoming schedule.",
        })
        return

    # Extract distinct available days
    seen_days = []
    for s in slots:
        d = s.get("date", "")
        if d and d not in seen_days:
            seen_days.append(d)

    week_summary = f"Openings available across {len(seen_days)} days this week: {', '.join(seen_days)}."
    DemoDisplay.tool_result(f"Found openings across {len(seen_days)} days ({', '.join(seen_days[:3])})")

    await params.result_callback(
        {
            "success": True,
            "week_summary": week_summary,
            "available_days": seen_days,
            "slots": [
                {
                    "weekday": s.get("weekday", ""),
                    "date": s["date"],
                    "time": s["time"],
                    "start_time": s["start_time"],
                }
                for s in slots
            ],
        }
    )


async def book_meeting(
    params: FunctionCallParams,
    meeting_type: str,
    start_time: str,
    customer_name: str,
    email: str,
    topic: str,
    phone: str = "",
):
    """Book a meeting once the caller has picked a specific open time and confirmed their
    details. `start_time` MUST be copied EXACTLY as it came back from check_availability's
    results — it's an internal identifier, don't reformat it, don't construct your own.

    Args:
        meeting_type: "sales" or "support" — same value used in the check_availability call
            that produced this start_time.
        start_time: The exact start_time string from check_availability's results.
        customer_name: Caller's name.
        email: Caller's email address — required, Cal.com's confirmation and the video
            link go here.
        topic: A SPECIFIC one-line summary of what will be discussed — e.g. "AI Automation
            pricing for a 10-person sales team" or "Invoice #4521 billing discrepancy", not
            a generic "sales call" or "support issue". This is what the rep sees before the
            meeting, so vague topics leave them unprepared.
        phone: Caller's phone number. Leave blank — the system already has it from caller
            ID, don't ask the caller for it.
    """
    app_resources = params.app_resources or {}
    call_id = app_resources.get("session_id")
    caller_phone = phone or app_resources.get("caller_phone") or ""
    email = _normalize_email(email)

    if not start_time:
        booking_url = os.getenv("CAL_BOOKING_URL", "")
        fallback_sent = False
        if email and booking_url:
            fb = await email_sender.send_booking_fallback_email(
                to=email,
                customer_name=customer_name,
                booking_url=booking_url,
            )
            fallback_sent = fb.get("success", False)
        await params.result_callback({
            "success": False,
            "error": "missing_start_time",
            "fallback_link_sent": fallback_sent,
        })
        return

    DemoDisplay.tool_action(f"Booking {meeting_type} meeting for {customer_name} ({email})...")
    result = await scheduler.book_slot(
        meeting_type=meeting_type,
        start_time=start_time,
        customer_name=customer_name,
        phone=caller_phone,
        email=email,
        topic=topic,
    )

    if not result["success"]:
        DemoDisplay.tool_result(f"Booking failed ({result['error']}) - sending fallback link")
        # Fallback: if we have the caller's email, send them a direct booking link so they
        # can still book themselves even when the API booking fails.
        booking_url = os.getenv("CAL_BOOKING_URL", "")
        fallback_sent = False
        if email and booking_url:
            fb = await email_sender.send_booking_fallback_email(
                to=email,
                customer_name=customer_name,
                booking_url=booking_url,
            )
            fallback_sent = fb["success"]

        await params.result_callback({
            "success": False,
            "error": result["error"],
            "fallback_link_sent": fallback_sent,
        })
        return

    date_str, time_str = scheduler.format_display(start_time)
    call_state = app_resources.get("call_state")
    if isinstance(call_state, dict):
        call_state["outcome"] = "meeting_booked"

    DemoDisplay.tool_result(f"Meeting booked successfully for {customer_name} on {date_str} at {time_str}!")
    await booking_db.log_meeting(
        meeting_type=meeting_type,
        customer_name=customer_name,
        phone=caller_phone,
        email=email,
        topic=topic,
        date=date_str,
        time_str=time_str,
        meet_link=result["meet_link"],
        call_id=call_id,
    )

    # Fire-and-forget confirmation email — never fails the booking if email fails.
    await email_sender.send_meeting_confirmation(
        to=email,
        customer_name=customer_name,
        date=date_str,
        time_str=time_str,
        meet_link=result["meet_link"],
        meeting_type=meeting_type,
    )

    await params.result_callback(
        {
            "success": True,
            "meet_link": result["meet_link"],
        }
    )


async def send_booking_link(params: FunctionCallParams, customer_name: str, email: str):
    """Email the caller a direct link to the booking page, WITHOUT creating a real meeting.
    Use this when the caller wants a link to book themselves instead of picking a time live
    on the call, or when no open slot from check_availability works for their schedule.

    Never call `book_meeting` with a slot the caller hasn't actually chosen just to trigger
    an email — that creates a real meeting they never agreed to. This tool is the correct
    way to "just send a link": it only sends an email, it never touches the calendar.

    Args:
        customer_name: Caller's name.
        email: Caller's email address — read it back and get an explicit "yes" from the
            caller before calling this, same as you would before book_meeting.
    """
    email = _normalize_email(email)
    booking_url = os.getenv("CAL_BOOKING_URL", "")

    app_resources = params.app_resources or {}
    call_state = app_resources.get("call_state")
    if isinstance(call_state, dict):
        call_state["outcome"] = "warm_lead"

    # A caller asking for an email link is a warm lead — log to leads table
    caller_phone = app_resources.get("caller_phone") or ""
    await booking_db.create_lead(
        name=customer_name or "Caller",
        phone=caller_phone,
        interested_service="Requested Booking Link via Email",
        reason=f"Sent booking link to {email}",
        call_id=app_resources.get("session_id"),
    )

    DemoDisplay.tool_action(f"Sending booking link email to {customer_name} ({email})...")
    if not email or not booking_url:
        DemoDisplay.tool_result("Failed: missing email or booking URL")
        await params.result_callback({"success": False, "error": "missing_email_or_url"})
        return

    result = await email_sender.send_booking_fallback_email(
        to=email,
        customer_name=customer_name,
        booking_url=booking_url,
    )
    if result["success"]:
        DemoDisplay.tool_result(f"Booking link email sent to {email}.")
    else:
        DemoDisplay.tool_result(f"Failed to send email: {result.get('error')}")
    await params.result_callback({"success": result["success"], "error": result.get("error")})


async def mark_potential_lead(
    params: FunctionCallParams,
    customer_name: str,
    interested_service: str,
    reason: str,
    phone: str = "",
):
    """Log a caller as a warm lead when they show real interest in a service but don't
    commit to booking right now. Only call this for genuine hesitation — "let me think
    about it", "I need to check budget", "I need to check with my team", "maybe later" —
    NOT for callers who explicitly say they're not interested (just end the call politely
    for those, don't log anything), and NOT for casual info-only questions with no purchase
    intent at all.

    Args:
        customer_name: Caller's name, if given. Use "Unknown" if they didn't share it.
        interested_service: Which service they were considering (Content Creation, AI
            Automation, AI Voice Agents, Web Development, or SEO).
        reason: Short note on why they didn't commit, e.g. "wants to discuss with partner
            first" or "comparing against another vendor".
        phone: Leave blank — the system already has it from caller ID.
    """
    app_resources = params.app_resources or {}
    caller_phone = phone or app_resources.get("caller_phone") or ""

    DemoDisplay.tool_action(f"Logging warm lead: {customer_name} ({interested_service})...")
    call_state = app_resources.get("call_state")
    if isinstance(call_state, dict):
        call_state["outcome"] = "warm_lead"

    result = await booking_db.create_lead(
        name=customer_name,
        phone=caller_phone,
        interested_service=interested_service,
        reason=reason,
        call_id=app_resources.get("session_id"),
    )
    DemoDisplay.tool_result(f"Lead saved to database (Reason: {reason})")
    await params.result_callback({"success": result["success"], "error": result["error"]})


async def transfer_to_human(params: FunctionCallParams, reason: str):
    """Transfer the caller to a live human team member right now. Use when the caller
    explicitly asks to speak to a person, an expert, or a human agent, or for urgent issues.

    Args:
        reason: Short reason a human was needed (e.g. "caller requested live specialist").
    """
    app_resources = params.app_resources or {}
    call_state = app_resources.get("call_state")
    if isinstance(call_state, dict):
        call_state["outcome"] = "transferred"

    transfer_phone = os.getenv("TRANSFER_PHONE_NUMBER", "").strip()
    telnyx_api_key = os.getenv("TELNYX_API_KEY", "").strip()
    call_control_id = app_resources.get("call_control_id")
    called_phone = app_resources.get("called_phone") or os.getenv("TELNYX_PHONE_NUMBER", "").strip()

    DemoDisplay.tool_action(f"Transfer requested by caller (Reason: {reason})")

    if not transfer_phone or not telnyx_api_key or not call_control_id:
        logger.warning(
            f"Live transfer requested but missing config or call_control_id "
            f"(transfer_phone={bool(transfer_phone)}, api_key={bool(telnyx_api_key)}, "
            f"call_control_id={call_control_id}, reason={reason})"
        )
        DemoDisplay.tool_result("Transfer failed: missing transfer configuration")
        await params.result_callback({
            "success": False,
            "transferred": False,
            "error": "transfer_not_configured",
            "reason_logged": reason,
        })
        return

    # Announce the transfer to the caller over TTS first
    announcement = "Please hold on for just a moment while I transfer you directly to our team."
    DemoDisplay.bot_said(announcement)
    DemoDisplay.tool_action(f"Transferring caller to {transfer_phone}...")
    await params.llm.push_frame(TTSSpeakFrame(announcement))

    # Give TTS audio 2.5 seconds to flush to the caller over the WebSocket leg before transferring
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
                resp_text = await resp.text()
                if resp.status in (200, 202):
                    logger.info(
                        f"Telnyx call transfer successfully initiated to {transfer_phone} "
                        f"for call_control_id={call_control_id}"
                    )
                    DemoDisplay.tool_result(f"Successfully transferred to {transfer_phone}!")
                    await params.result_callback({
                        "success": True,
                        "transferred": True,
                        "transferred_to": transfer_phone,
                    })
                    # Signal shutdown of the AI bot worker since the call is now bridged to human
                    await params.llm.push_frame(EndWorkerFrame(), FrameDirection.UPSTREAM)
                    return
                else:
                    logger.error(
                        f"Telnyx transfer API failed (HTTP {resp.status}): {resp_text}"
                    )
                    DemoDisplay.tool_result(f"Transfer failed: HTTP {resp.status}")
                    await params.result_callback({
                        "success": False,
                        "transferred": False,
                        "error": f"telnyx_error_{resp.status}",
                    })
    except Exception as e:
        logger.error(f"Telnyx transfer request failed with exception: {e}")
        DemoDisplay.tool_result(f"Transfer error: {e}")
        await params.result_callback({
            "success": False,
            "transferred": False,
            "error": "transfer_request_exception",
        })


async def end_call(params: FunctionCallParams, farewell: str):
    """End the call politely. Use once the caller has said goodbye and there's nothing left
    to do — e.g. right after a meeting is booked and the caller signs off, after logging a
    lead, or if they explicitly ask to hang up.

    The `farewell` you pass here is SPOKEN ALOUD to the caller before the line disconnects,
    so it must be a complete, warm sign-off — never empty, never a placeholder. Do not also
    say goodbye in your own reply text before calling this; this tool speaks the goodbye for
    you, so saying it twice would make the caller hear it twice.

    Args:
        farewell: The exact goodbye line to speak before hanging up, e.g. "Perfect — you're
            all set for Tuesday at 2 PM. Thanks for calling Innoventix Hub, have a great
            day!" Keep it to one or two warm sentences, tailored to how the call went.
    """
    # Speak the goodbye FIRST, then signal shutdown — and push the end frame UPSTREAM, not
    # downstream. EndWorkerFrame tears the pipeline down, so if it were pushed straight from
    # here (downstream, toward TTS/transport) it could race past the farewell audio and cut
    # the line before the caller hears it. Pushed upstream, it travels to the start of the
    # pipeline and flows back down through every stage in order, landing BEHIND the
    # already-queued farewell — so the audio plays out before the pipeline tears down. This
    # mirrors Pipecat's documented graceful-termination pattern.
    DemoDisplay.tool_action("Ending call with farewell...")
    DemoDisplay.bot_said(farewell)
    await params.result_callback({"success": True})
    await params.llm.push_frame(TTSSpeakFrame(farewell))
    await params.llm.push_frame(EndWorkerFrame(), FrameDirection.UPSTREAM)


INNOVENTIX_TOOLS = [
    check_availability,
    book_meeting,
    send_booking_link,
    mark_potential_lead,
    transfer_to_human,
    end_call,
]
