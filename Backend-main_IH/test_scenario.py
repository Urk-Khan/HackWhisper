"""
End-to-end test scenarios for HackWhisper: AI Hacktoberfest Voice Calling Agent.

Scenario A — City Event Search + SMS Dispatch (User asks for events in Kolkata, gets registration link texted)
Scenario B — General Hacktoberfest Inquiry (Rules, eligibility, PR requirements, rewards)
Scenario C — City Event Search + Email Dispatch (User asks for events in London, gets email confirmation)
Scenario D — Detailed Event Schedule & Venue Lookup
Scenario E — Organizer / Human Transfer

Run: python test_scenario.py
"""

import asyncio
import json
import os
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from dotenv import load_dotenv

load_dotenv(override=True)

import openai
import event_manager
import sms_sender
import email_sender
from prompts import HACKTOBERFEST_SYSTEM_PROMPT, get_system_prompt
from tools import _normalize_email

SCENARIOS = {
    "A — City Event Search & SMS Dispatch": {
        "context": None,
        "turns": [
            "Hi, what Hacktoberfest events are happening in Kolkata this year?",
            "That sounds great. Can you send me the registration link on SMS to my phone number?",
            "Yes, please text it to this number.",
        ],
    },
    "B — General Info Inquiry (Rules & Rewards)": {
        "context": None,
        "turns": [
            "Hey, can you explain what Hacktoberfest is?",
            "How many pull requests do I need to make to complete the challenge?",
            "Are beginners allowed to join, or is it only for experienced developers?",
            "Thanks for the info, that's really helpful!",
        ],
    },
    "C — Event Search & Email Dispatch": {
        "context": None,
        "turns": [
            "Hello, are there any Hacktoberfest events in London?",
            "Can you email me the event details and registration link?",
            "My name is Alex and my email is alex dot developer at gmail dot com.",
            "Yes, that email address is correct.",
        ],
    },
    "D — Event Details & Schedule Lookup": {
        "context": None,
        "turns": [
            "I'm looking for event details for the Hack Day in Kolkata.",
            "What format is it and what time does it start?",
            "Great, thanks for the details!",
        ],
    },
    "E — Live Organizer Transfer Request": {
        "context": None,
        "turns": [
            "Hi, I'm having trouble with our community venue sponsorship and I need to speak to an event organizer right away.",
        ],
    },
}

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "search_events",
            "description": "Search for Hacktoberfest events taking place in a specific city, state, or region.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_event_details",
            "description": "Retrieve full schedule, venue address, format, and description for a specific event.",
            "parameters": {
                "type": "object",
                "properties": {"event_id": {"type": "string"}},
                "required": ["event_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_registration_link",
            "description": "Get the official registration link and website for an event.",
            "parameters": {
                "type": "object",
                "properties": {"event_id": {"type": "string"}},
                "required": ["event_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_event_email",
            "description": "Email Hacktoberfest event details and registration link to the caller.",
            "parameters": {
                "type": "object",
                "properties": {
                    "email": {"type": "string"},
                    "event_id": {"type": "string"},
                    "attendee_name": {"type": "string"},
                },
                "required": ["email", "event_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_event_sms",
            "description": "Send Hacktoberfest event details and registration link directly via SMS.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string"},
                    "phone_number": {"type": "string"},
                },
                "required": ["event_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "transfer_to_human",
            "description": "Transfer caller to a human organizer or support specialist.",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "end_call",
            "description": "Politely end the phone call with a warm farewell.",
            "parameters": {
                "type": "object",
                "properties": {"farewell": {"type": "string"}},
                "required": ["farewell"],
            },
        },
    },
]


async def handle_tool(name: str, args: dict) -> str:
    """Execute each Hacktoberfest tool and print execution details."""
    if name == "search_events":
        city = args.get("city", "")
        print(f"  [TOOL] search_events(city='{city}')")
        results = await event_manager.search_events_by_city(city, limit=4)
        print(f"  [RESULT] Found {len(results)} matching events.")
        return json.dumps({
            "success": True,
            "found": len(results) > 0,
            "city": city,
            "count": len(results),
            "events": results,
        })

    elif name == "get_event_details":
        event_id = args.get("event_id", "")
        print(f"  [TOOL] get_event_details(event_id='{event_id[:16]}')")
        ev = await event_manager.get_event_by_id_or_name(event_id)
        if ev:
            print(f"  [RESULT] Event: {ev['name']}")
            return json.dumps({"success": True, **ev})
        return json.dumps({"success": False, "error": "not_found"})

    elif name == "get_registration_link":
        event_id = args.get("event_id", "")
        print(f"  [TOOL] get_registration_link(event_id='{event_id[:16]}')")
        ev = await event_manager.get_event_by_id_or_name(event_id)
        url = ev.get("registration_url") if ev else "https://hacktoberfest.com"
        print(f"  [RESULT] URL: {url}")
        return json.dumps({"success": True, "registration_url": url})

    elif name == "send_event_email":
        email = _normalize_email(args.get("email", ""))
        event_id = args.get("event_id", "")
        name_str = args.get("attendee_name", "Alex")
        print(f"  [TOOL] send_event_email(email='{email}', event_id='{event_id[:16]}')")
        ev = await event_manager.get_event_by_id_or_name(event_id)
        if ev:
            res = await email_sender.send_event_email(
                to=email,
                customer_name=name_str,
                event_name=ev["name"],
                starts_at_str=ev.get("starts_at_str", ""),
                city=ev.get("city", ""),
                venue=ev.get("address", ""),
                description=ev.get("description", ""),
                registration_url=ev.get("registration_url", ""),
            )
            print(f"  [EMAIL DISPATCH] success={res.get('success')}")
            return json.dumps({"success": res.get("success"), "email": email})
        return json.dumps({"success": False, "error": "event_not_found"})

    elif name == "send_event_sms":
        phone = args.get("phone_number") or "+15075010702"
        event_id = args.get("event_id", "")
        print(f"  [TOOL] send_event_sms(phone='{phone}', event_id='{event_id[:16]}')")
        ev = await event_manager.get_event_by_id_or_name(event_id)
        if ev:
            res = await sms_sender.send_event_sms(
                to_phone=phone,
                event_name=ev["name"],
                starts_at_str=ev.get("starts_at_str", ""),
                city=ev.get("city", ""),
                registration_url=ev.get("registration_url", ""),
            )
            print(f"  [SMS DISPATCH] success={res.get('success')}")
            return json.dumps({"success": res.get("success"), "phone": phone})
        return json.dumps({"success": False, "error": "event_not_found"})

    elif name == "transfer_to_human":
        reason = args.get("reason", "")
        print(f"  [TOOL] transfer_to_human(reason='{reason}')")
        return json.dumps({"success": True, "transferred": True, "target": "+19177954404"})

    elif name == "end_call":
        farewell = args.get("farewell", "")
        print(f"  [TOOL] end_call(farewell='{farewell}')")
        return json.dumps({"success": True, "call_ended": True})

    return json.dumps({"error": f"Unknown tool {name}"})


async def run_scenario(name: str, sc: dict):
    print(f"\n{'='*70}\nRunning: {name}\n{'='*70}")
    await event_manager.sync_events()

    client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    messages = [{"role": "system", "content": get_system_prompt()}]
    if sc.get("context"):
        messages.append({"role": "user", "content": sc["context"]})

    for user_turn in sc["turns"]:
        print(f"\nUser: {user_turn}")
        messages.append({"role": "user", "content": user_turn})

        while True:
            resp = await client.chat.completions.create(
                model=model,
                messages=messages,
                tools=TOOLS_SCHEMA,
                tool_choice="auto",
                temperature=0.3,
                max_tokens=150,
            )
            msg = resp.choices[0].message
            messages.append(msg)

            if msg.content:
                print(f"Whisper: {msg.content}")

            if msg.tool_calls:
                for tc in msg.tool_calls:
                    fn_name = tc.function.name
                    fn_args = json.loads(tc.function.arguments)
                    result_str = await handle_tool(fn_name, fn_args)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_str,
                    })
                continue
            break


async def main():
    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set in .env. Skipping live LLM scenario tests.")
        return

    for sc_name, sc in SCENARIOS.items():
        try:
            await run_scenario(sc_name, sc)
        except Exception as e:
            print(f"Error in {sc_name}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
