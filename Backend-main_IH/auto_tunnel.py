"""
Automates the two manual steps that used to precede every test call:
  1. Start a cloudflared quick tunnel pointing at this server.
  2. Tell Telnyx the new public URL (cloudflared gives a new random URL every restart).

With this, `python bot.py` is the only command you need.

How it works: Pipecat's own dev runner (pipecat.runner.run) has a built-in route (`POST /`)
that returns the correct TeXML for Telnyx, generated from a `--proxy <host>` value passed on
the command line. So the only moving part is: start the tunnel, grab its hostname, and (a)
pass it to the runner as --proxy, (b) tell Telnyx's TeXML Application to fetch TeXML from
that tunnel's root URL.

Requires:
  - `cloudflared` in your PATH, or `cloudflared.exe` (Windows) / `cloudflared` (Linux/macOS)
    sitting next to this file.
  - TELNYX_API_KEY and TELNYX_TEXML_APP_ID in .env (run `python list_texml_apps.py` to find
    your App ID — reading it from .env, not hardcoding it, is what this file does
    differently from an earlier version of this automation that hardcoded an App ID and
    silently broke when a different TeXML Application ended up assigned to the number).

If either isn't set up, `python bot.py` still runs the server locally — it just logs a
warning and you're back to doing the tunnel/portal update by hand.
"""

import asyncio
import base64
import os
import re
import shutil
import sys
from pathlib import Path

import aiohttp
from dotenv import load_dotenv
from loguru import logger

load_dotenv(override=True)

TUNNEL_URL_PATTERN = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
TUNNEL_STARTUP_TIMEOUT_SECS = 25


def _find_cloudflared() -> str | None:
    local_name = "cloudflared.exe" if sys.platform == "win32" else "cloudflared"
    local_path = Path(__file__).parent / local_name
    if local_path.exists():
        return str(local_path)
    return shutil.which("cloudflared")


async def _start_tunnel(port: int) -> tuple[asyncio.subprocess.Process, str]:
    cloudflared_path = _find_cloudflared()
    if not cloudflared_path:
        raise RuntimeError(
            "cloudflared not found. Put cloudflared.exe next to bot.py, or install it and "
            "make sure it's on your PATH: https://developers.cloudflare.com/cloudflare-one/"
            "connections/connect-networks/downloads/"
        )

    logger.info(f"Starting cloudflared tunnel -> http://localhost:{port} ...")
    process = await asyncio.create_subprocess_exec(
        cloudflared_path,
        "tunnel",
        "--url",
        f"http://localhost:{port}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    async def _read_until_url_found() -> str:
        assert process.stdout is not None
        async for raw_line in process.stdout:
            line = raw_line.decode(errors="replace").rstrip()
            match = TUNNEL_URL_PATTERN.search(line)
            if match:
                return match.group(0)
        raise RuntimeError("cloudflared exited before printing a tunnel URL.")

    try:
        url = await asyncio.wait_for(_read_until_url_found(), timeout=TUNNEL_STARTUP_TIMEOUT_SECS)
    except asyncio.TimeoutError as e:
        process.terminate()
        raise RuntimeError(
            f"cloudflared didn't print a tunnel URL within {TUNNEL_STARTUP_TIMEOUT_SECS}s. "
            "Check that cloudflared is working: run it manually to see its output."
        ) from e

    logger.info(f"Tunnel ready: {url}")
    return process, url


async def _update_signalwire_voice_url(public_url: str) -> None:
    space_url = os.getenv("SIGNALWIRE_SPACE_URL")
    project_id = os.getenv("SIGNALWIRE_PROJECT_ID")
    api_token = os.getenv("SIGNALWIRE_API_TOKEN")
    phone_number = os.getenv("SIGNALWIRE_PHONE_NUMBER")

    if not space_url or not project_id or not api_token:
        logger.warning(
            "SIGNALWIRE_SPACE_URL, SIGNALWIRE_PROJECT_ID, or SIGNALWIRE_API_TOKEN not set — "
            "skipping automatic SignalWire update."
        )
        return

    space_url = space_url.rstrip("/")
    auth_header = "Basic " + base64.b64encode(f"{project_id}:{api_token}".encode()).decode()
    headers = {"Authorization": auth_header}

    async with aiohttp.ClientSession(headers=headers) as session:
        list_url = f"{space_url}/api/laml/2010-04-01/Accounts/{project_id}/IncomingPhoneNumbers.json"
        try:
            async with session.get(list_url) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    logger.error(f"Failed to fetch SignalWire incoming numbers ({resp.status}): {body}")
                    return
                data = await resp.json()
        except Exception as e:
            logger.error(f"Error connecting to SignalWire API: {e}")
            return

        numbers = data.get("incoming_phone_numbers", [])
        if not numbers:
            logger.warning("No incoming phone numbers found on this SignalWire account.")
            return

        target_sid = None
        target_num_str = None
        if phone_number:
            clean_target = phone_number.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
            for item in numbers:
                item_num = item.get("phone_number", "").replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
                if item_num == clean_target or clean_target in item_num:
                    target_sid = item.get("sid")
                    target_num_str = item.get("phone_number")
                    break

        if not target_sid:
            target_sid = numbers[0].get("sid")
            target_num_str = numbers[0].get("phone_number")
            logger.info(f"Targeting SignalWire number: {target_num_str} (SID: {target_sid})")

        update_url = f"{space_url}/api/laml/2010-04-01/Accounts/{project_id}/IncomingPhoneNumbers/{target_sid}.json"
        payload = {
            "VoiceUrl": f"{public_url}/",
            "VoiceMethod": "POST",
        }
        try:
            async with session.post(update_url, data=payload) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    logger.error(f"Failed to update SignalWire Voice URL ({resp.status}): {body}")
                else:
                    logger.info(
                        f"SignalWire number {target_num_str} updated — Voice URL = {public_url}/"
                    )
        except Exception as e:
            logger.error(f"Error updating SignalWire Voice URL: {e}")


async def _update_telnyx_voice_url(public_url: str) -> None:
    api_key = os.getenv("TELNYX_API_KEY")
    app_id = os.getenv("TELNYX_TEXML_APP_ID")

    if not api_key or not app_id:
        logger.warning(
            "TELNYX_API_KEY or TELNYX_TEXML_APP_ID not set — skipping automatic Telnyx "
            "update. Set both in .env (run `python list_texml_apps.py` to find your App "
            f"ID), or update the TeXML Application's Voice URL by hand: {public_url}/"
        )
        return

    url = f"https://api.telnyx.com/v2/texml_applications/{app_id}"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"voice_url": f"{public_url}/"}

    async with aiohttp.ClientSession() as session:
        async with session.patch(url, json=payload, headers=headers) as resp:
            if resp.status >= 400:
                body = await resp.text()
                logger.error(f"Failed to update Telnyx TeXML Application ({resp.status}): {body}")
                logger.warning(f"Update it manually in the portal instead: Voice URL = {public_url}/")
            else:
                logger.info(f"Telnyx TeXML Application updated — Voice URL = {public_url}/")


async def _update_telephony_voice_url(public_url: str) -> None:
    if os.getenv("SIGNALWIRE_PROJECT_ID") and os.getenv("SIGNALWIRE_SPACE_URL"):
        await _update_signalwire_voice_url(public_url)
    elif os.getenv("TELNYX_API_KEY") and os.getenv("TELNYX_TEXML_APP_ID"):
        await _update_telnyx_voice_url(public_url)
    else:
        logger.warning(
            "Neither SignalWire nor Telnyx credentials found in .env — skipping automatic webhook update."
        )


async def setup_tunnel_and_telephony(port: int) -> tuple[asyncio.subprocess.Process | None, str | None]:
    """Returns (cloudflared_process, hostname) — hostname is None (server still runs, just
    not publicly reachable) if anything here fails."""
    # Check if an explicit public domain/URL is provided (e.g. deployed on Coolify, VPS, etc.)
    explicit_public_url = (
        os.getenv("PUBLIC_URL")
        or os.getenv("SERVER_URL")
        or os.getenv("COOLIFY_FQDN")
        or os.getenv("DOMAIN")
    )
    if explicit_public_url:
        explicit_public_url = explicit_public_url.strip()
        if not explicit_public_url.startswith("http://") and not explicit_public_url.startswith("https://"):
            explicit_public_url = f"https://{explicit_public_url}"
        logger.info(f"Using configured public URL: {explicit_public_url}")
        await _update_telephony_voice_url(explicit_public_url)
        hostname = (
            explicit_public_url.replace("https://", "").replace("http://", "").rstrip("/")
        )
        return None, hostname

    if os.getenv("DISABLE_TUNNEL", "").lower() in ("1", "true", "yes"):
        logger.info("Tunnel startup skipped via DISABLE_TUNNEL.")
        return None, None

    try:
        process, public_url = await _start_tunnel(port)
    except Exception as e:
        logger.error(f"Tunnel setup failed, continuing with local-only server: {e}")
        return None, None

    await _update_telephony_voice_url(public_url)

    hostname = public_url.replace("https://", "").replace("http://", "")
    return process, hostname


# Backward-compatible alias
setup_tunnel_and_telnyx = setup_tunnel_and_telephony

