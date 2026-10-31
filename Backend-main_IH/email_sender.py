"""
Sends meeting confirmation and fallback-booking-link emails via Resend
(https://resend.com) — a single REST call, no SDK. Chosen over the Gmail API specifically to
avoid domain-wide delegation setup (Gmail API sending "as" a real mailbox needs Google
Workspace admin access to grant that, which is more setup than a small team typically wants
for this). If you'd rather send from a real Innoventix Gmail address, swap this module for a
Gmail API call — everything else in the project is unaffected, this is the only file that
would change.

ACTIVE — both functions here are called unconditionally from tools.py (book_meeting on
success calls send_meeting_confirmation; book_meeting on failure and the standalone
send_booking_link tool both call send_booking_fallback_email). This is NOT redundant with
Cal.com/Calendly's own native confirmation emails — those come from the scheduling provider
directly if you've enabled them in its settings, this module sends Innoventix Hub's own
branded email on top of (or instead of) that.

Requires a Resend account + API key, and a "from" address on a domain you've verified with
Resend (their sandbox domain works for testing without domain verification).
"""

import os

import aiohttp
from loguru import logger

_RESEND_API_URL = "https://api.resend.com/emails"
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=8)


async def send_meeting_confirmation(
    *,
    to: str,
    customer_name: str,
    date: str,
    time_str: str,
    meet_link: str | None,
    meeting_type: str,
) -> dict:
    """Returns {"success": bool, "error": str | None}. Never raises — a failed confirmation
    email shouldn't crash the booking flow; the caller already heard the booking confirmed
    on the call itself, this is a nice-to-have follow-up."""
    resend_api_key = os.getenv("RESEND_API_KEY", "")
    resend_from_address = os.getenv("RESEND_FROM_ADDRESS", "")

    if not resend_api_key or not resend_from_address:
        logger.error("RESEND_API_KEY or RESEND_FROM_ADDRESS not set in .env")
        return {"success": False, "error": "email_not_configured"}

    if not to:
        logger.warning("send_meeting_confirmation called with no recipient email — skipping")
        return {"success": False, "error": "missing_email"}

    meet_line = (
        f'<p><a href="{meet_link}">Join on Google Meet</a></p>'
        if meet_link
        else "<p>A calendar invite is on its way separately.</p>"
    )
    label = "Support Call" if meeting_type.lower() == "support" else "Meeting"

    html = f"""
    <p>Hi {customer_name},</p>
    <p>Your {label.lower()} with Innoventix Hub is confirmed for <strong>{date} at {time_str}</strong>.</p>
    {meet_line}
    <p>Looking forward to speaking with you.</p>
    <p>— Innoventix Hub</p>
    """

    payload = {
        "from": resend_from_address,
        "to": [to],
        "subject": f"Innoventix Hub — {label} Confirmed: {date} at {time_str}",
        "html": html,
    }
    headers = {"Authorization": f"Bearer {resend_api_key}", "Content-Type": "application/json"}

    try:
        async with aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT) as session:
            async with session.post(_RESEND_API_URL, json=payload, headers=headers) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    logger.error(f"Resend send failed ({resp.status}): {body}")
                    return {"success": False, "error": "email_send_failed"}
    except Exception as e:
        logger.error(f"Resend request failed: {e}")
        return {"success": False, "error": "email_unreachable"}

    logger.info(f"Confirmation email sent to {to}")
    return {"success": True, "error": None}


async def send_booking_fallback_email(
    *,
    to: str,
    customer_name: str,
    booking_url: str,
) -> dict:
    """Emails the caller a direct link to book themselves — used both as a fallback when the
    scheduling-provider API booking call fails, and proactively via the send_booking_link
    tool when the caller wants a link instead of booking live on the call. Returns
    {"success": bool, "error": str | None}. Never raises — a failed email must never surface
    as an error on the call."""
    resend_api_key = os.getenv("RESEND_API_KEY", "")
    resend_from_address = os.getenv("RESEND_FROM_ADDRESS", "")

    if not resend_api_key or not resend_from_address:
        logger.error("RESEND_API_KEY or RESEND_FROM_ADDRESS not set — cannot send fallback email")
        return {"success": False, "error": "email_not_configured"}

    if not to:
        logger.warning("send_booking_fallback_email called with no recipient email — skipping")
        return {"success": False, "error": "missing_email"}

    html = f"""
    <p>Hi {customer_name},</p>
    <p>Thanks for calling Innoventix Hub! We had a small hiccup completing your booking on our end,
    but you can lock in your preferred time directly using the link below — it only takes a minute:</p>
    <p style="margin: 24px 0;">
        <a href="{booking_url}" style="background:#2563eb;color:#fff;padding:12px 24px;border-radius:6px;text-decoration:none;font-weight:bold;">
            Book Your Meeting
        </a>
    </p>
    <p>If you have any trouble, just call us back and we'll sort it out straight away.</p>
    <p>— Innoventix Hub</p>
    """

    payload = {
        "from": resend_from_address,
        "to": [to],
        "subject": "Innoventix Hub — Complete Your Booking",
        "html": html,
    }
    headers = {"Authorization": f"Bearer {resend_api_key}", "Content-Type": "application/json"}

    try:
        async with aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT) as session:
            async with session.post(_RESEND_API_URL, json=payload, headers=headers) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    logger.error(f"Resend fallback send failed ({resp.status}): {body}")
                    return {"success": False, "error": "email_send_failed"}
    except Exception as e:
        logger.error(f"Resend fallback request failed: {e}")
        return {"success": False, "error": "email_unreachable"}

    logger.info(f"Fallback booking link email sent to {to}")
    return {"success": True, "error": None}
