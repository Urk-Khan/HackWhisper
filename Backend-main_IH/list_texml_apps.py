"""
One-off helper: lists your Telnyx TeXML Applications and their IDs, so you can copy the
right one into .env as TELNYX_TEXML_APP_ID.

Run:
    python list_texml_apps.py
"""

import asyncio
import os

import aiohttp
from dotenv import load_dotenv

load_dotenv(override=True)


async def main():
    api_key = os.getenv("TELNYX_API_KEY")
    if not api_key:
        print("TELNYX_API_KEY is not set in .env")
        return

    headers = {"Authorization": f"Bearer {api_key}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(
            "https://api.telnyx.com/v2/texml_applications", headers=headers
        ) as resp:
            if resp.status >= 400:
                print(f"Request failed ({resp.status}): {await resp.text()}")
                return
            data = await resp.json()

    apps = data.get("data", [])
    if not apps:
        print("No TeXML Applications found on this account. Create one in the Telnyx portal first.")
        return

    print(f"Found {len(apps)} TeXML Application(s):\n")
    for app in apps:
        print(f"  id:            {app.get('id')}")
        print(f"  friendly_name: {app.get('friendly_name')}")
        print(f"  voice_url:     {app.get('voice_url')}")
        print()
    print("Copy the `id` of the app you're using into .env as TELNYX_TEXML_APP_ID.")


if __name__ == "__main__":
    asyncio.run(main())
