"""
SMS Dispatcher for HackWhisper Voice Agent.

Sends Hacktoberfest event information and registration links via SMS to callers
using Telnyx (or SignalWire) REST API.
"""

import os
import re
import sys
from typing import Optional
import aiohttp
from loguru import logger

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_TELNYX_MESSAGES_URL = "https://api.telnyx.com/v2/messages"
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=8)


def _normalize_phone(raw: str) -> str:
    """Cleans phone numbers into E.164 standard format."""
    if not raw:
        return ""
    digits = re.sub(r"[^\d+]", "", raw.strip())
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    elif not digits.startswith("+"):
        if len(digits) == 10:
            digits = "+1" + digits
        elif len(digits) == 11 and digits.startswith("1"):
            digits = "+" + digits
        else:
            digits = "+" + digits
    return digits


async def send_event_sms(
    to_phone: str,
    event_name: str,
    starts_at_str: str,
    city: str,
    registration_url: str,
    website_url: Optional[str] = None,
) -> dict:
    """
    Sends an SMS with Hacktoberfest event details and registration link.
    Returns: {"success": bool, "error": str | None}
    """
    clean_to = _normalize_phone(to_phone)
    if not clean_to:
        logger.warning("send_event_sms called with no valid recipient phone number.")
        return {"success": False, "error": "invalid_phone_number"}

    telnyx_api_key = os.getenv("TELNYX_API_KEY", "").strip()
    from_phone = os.getenv("TELNYX_PHONE_NUMBER", "").strip()

    sw_space = os.getenv("SIGNALWIRE_SPACE_URL", "").rstrip("/")
    sw_project = os.getenv("SIGNALWIRE_PROJECT_ID", "").strip()
    sw_token = os.getenv("SIGNALWIRE_API_TOKEN", "").strip()
    sw_from = os.getenv("SIGNALWIRE_PHONE_NUMBER", "").strip()

    loc_str = f" in {city}" if city else ""
    url = registration_url or website_url or "https://hacktoberfest.com/fests"

    message_text = (
        f"Hacktoberfest Event Details:\n"
        f"Event: {event_name}{loc_str}\n"
        f"Date: {starts_at_str}\n"
        f"Register: {url}\n\n"
        f"Happy Hacking! - HackWhisper Team"
    )

    if telnyx_api_key and from_phone:
        payload = {
            "from": from_phone,
            "to": clean_to,
            "text": message_text,
        }
        headers = {
            "Authorization": f"Bearer {telnyx_api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT) as session:
                async with session.post(_TELNYX_MESSAGES_URL, json=payload, headers=headers) as resp:
                    if resp.status in (200, 201, 202):
                        logger.info(f"SMS successfully dispatched via Telnyx to {clean_to}")
                        return {"success": True, "error": None, "message": message_text}
                    body = await resp.text()
                    logger.error(f"Telnyx SMS dispatch failed ({resp.status}): {body}")
                    return {"success": False, "error": f"telnyx_error_{resp.status}"}
        except Exception as e:
            logger.error(f"Telnyx SMS request exception: {e}")
            return {"success": False, "error": "network_exception"}

    elif sw_space and sw_project and sw_token and sw_from:
        url = f"{sw_space}/api/laml/2010-04-01/Accounts/{sw_project}/Messages.json"
        auth = aiohttp.BasicAuth(sw_project, sw_token)
        data = {"From": sw_from, "To": clean_to, "Body": message_text}
        try:
            async with aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT) as session:
                async with session.post(url, auth=auth, data=data) as resp:
                    if resp.status in (200, 201):
                        logger.info(f"SMS successfully dispatched via SignalWire to {clean_to}")
                        return {"success": True, "error": None, "message": message_text}
                    body = await resp.text()
                    logger.error(f"SignalWire SMS dispatch failed: {body}")
                    return {"success": False, "error": f"signalwire_error_{resp.status}"}
        except Exception as e:
            logger.error(f"SignalWire SMS request exception: {e}")
            return {"success": False, "error": "network_exception"}

    else:
        # Nothing is sent without credentials, so report the failure instead of pretending.
        logger.error(
            "SMS not sent: TELNYX_API_KEY + TELNYX_PHONE_NUMBER (or SIGNALWIRE credentials) are not set."
        )
        return {"success": False, "error": "sms_not_configured"}
