"""
Sends Hacktoberfest Event details and registration links via:
1. Direct Gmail SMTP (if GMAIL_USER and GMAIL_APP_PASSWORD are provided)
2. Resend API (fallback / default)
"""

import asyncio
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import os
import smtplib
from typing import Optional
import aiohttp
from loguru import logger

_RESEND_API_URL = "https://api.resend.com/emails"
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=8)


def _send_via_gmail_sync(
    gmail_user: str,
    gmail_app_password: str,
    to: str,
    subject: str,
    html_content: str,
) -> dict:
    """Synchronous Gmail SMTP sender executed inside asyncio.to_thread."""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"HackWhisper <{gmail_user}>"
        msg["To"] = to

        part = MIMEText(html_content, "html")
        msg.attach(part)

        # Connect to Gmail SMTP server on port 587 (TLS)
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(gmail_user, gmail_app_password)
            server.sendmail(gmail_user, [to], msg.as_string())

        logger.info(f"Event email successfully dispatched via Gmail SMTP to {to}")
        return {"success": True, "error": None}
    except Exception as e:
        logger.error(f"Gmail SMTP dispatch failed: {e}")
        return {"success": False, "error": str(e)}


async def send_event_email(
    *,
    to: str,
    customer_name: str,
    event_name: str,
    starts_at_str: str,
    city: str,
    venue: str,
    description: str,
    registration_url: str,
    website_url: Optional[str] = None,
) -> dict:
    """
    Sends an email containing Hacktoberfest event information and registration link.
    Returns: {"success": bool, "error": str | None}
    """
    if not to:
        logger.warning("send_event_email called with no recipient email.")
        return {"success": False, "error": "missing_recipient_email"}

    reg_url = registration_url or website_url or "https://hacktoberfest.com/fests"
    location_str = f"{city} ({venue})" if (city and venue) else (city or venue or "Virtual / Online")

    subject = f"Hacktoberfest Event Details: {event_name}"

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <title>Your Hacktoberfest Event Details</title>
      <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #0b0c10; color: #e0e0e0; margin: 0; padding: 24px; }}
        .card {{ max-width: 580px; margin: auto; background-color: #1f2833; border: 1px solid #45a29e; border-radius: 8px; padding: 28px; box-shadow: 0 4px 12px rgba(0,0,0,0.5); }}
        .header {{ color: #66fcf1; font-size: 24px; font-weight: bold; margin-bottom: 12px; }}
        .sub {{ color: #c5c6c7; font-size: 15px; line-height: 1.5; }}
        .event-box {{ background-color: #0b0c10; border-left: 4px solid #66fcf1; padding: 16px; margin: 20px 0; border-radius: 4px; }}
        .event-title {{ font-size: 18px; font-weight: bold; color: #ffffff; margin-bottom: 8px; }}
        .detail-line {{ margin: 6px 0; font-size: 14px; color: #c5c6c7; }}
        .btn {{ display: inline-block; background-color: #ff6b4a; color: #ffffff; text-decoration: none; padding: 12px 24px; border-radius: 6px; font-weight: bold; margin-top: 14px; text-align: center; }}
        .footer {{ margin-top: 24px; font-size: 12px; color: #888888; text-align: center; }}
      </style>
    </head>
    <body>
      <div class="card">
        <div class="header">Hacktoberfest Event Details</div>
        <p class="sub">Hi {customer_name or 'Hacker'},</p>
        <p class="sub">Here are the event details and registration link you requested from our Hacktoberfest AI Voice Assistant:</p>
        
        <div class="event-box">
          <div class="event-title">{event_name}</div>
          <div class="detail-line"><strong>Date & Time:</strong> {starts_at_str}</div>
          <div class="detail-line"><strong>Location:</strong> {location_str}</div>
          {f'<div class="detail-line" style="margin-top: 10px;"><em>{description[:280]}...</em></div>' if description else ''}
          <div style="margin-top: 16px;">
            <a href="{reg_url}" class="btn" target="_blank">Register for Event &rarr;</a>
          </div>
        </div>

        <p class="sub">Remember: You can contribute to open-source repositories throughout October to earn badges and digital rewards on hacktoberfest.com.</p>
        <p class="sub">Happy Hacking!</p>
        <div class="footer">— HackWhisper AI Assistant</div>
      </div>
    </body>
    </html>
    """

    # 1. Priority 1: Check if Gmail SMTP credentials exist
    gmail_user = os.getenv("GMAIL_USER", "").strip()
    gmail_app_password = os.getenv("GMAIL_APP_PASSWORD", "").strip()

    if gmail_user and gmail_app_password:
        return await asyncio.to_thread(
            _send_via_gmail_sync,
            gmail_user,
            gmail_app_password,
            to,
            subject,
            html,
        )

    # 2. Priority 2: Resend API
    resend_api_key = os.getenv("RESEND_API_KEY", "").strip()
    resend_from_address = os.getenv(
        "RESEND_FROM_ADDRESS", "Hacktoberfest Assistant <onboarding@resend.dev>"
    ).strip()

    if resend_api_key:
        payload = {
            "from": resend_from_address,
            "to": [to],
            "subject": subject,
            "html": html,
        }
        headers = {"Authorization": f"Bearer {resend_api_key}", "Content-Type": "application/json"}
        try:
            async with aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT) as session:
                async with session.post(_RESEND_API_URL, json=payload, headers=headers) as resp:
                    if resp.status in (200, 201, 202):
                        logger.info(f"Hacktoberfest event email successfully sent via Resend to {to}")
                        return {"success": True, "error": None}
                    body = await resp.text()
                    logger.error(f"Resend email dispatch failed ({resp.status}): {body}")
                    return {"success": False, "error": f"resend_error_{resp.status}"}
        except Exception as e:
            logger.error(f"Resend request failed: {e}")
            return {"success": False, "error": "email_network_error"}

    # Mock mode if neither is configured
    logger.warning("Neither Gmail credentials nor RESEND_API_KEY set. Simulating email dispatch.")
    return {"success": True, "mock": True, "error": None}
