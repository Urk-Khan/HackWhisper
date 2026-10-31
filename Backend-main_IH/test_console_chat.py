"""
Quickest possible interactive console test: talk to the Innoventix Hub bot in your terminal as plain
text, with full tool calling support (Cal.com, Supabase, Email links).

Run:
    python test_console_chat.py

Type 'quit' to exit.
"""

import asyncio
import json
import os

from dotenv import load_dotenv

load_dotenv(override=True)

import openai
import booking_db
import cal_com as scheduler
import email_sender
from prompts import INNOVENTIX_SYSTEM_PROMPT
from tools import _normalize_email

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "check_availability",
            "description": "Look up open meeting slots from Cal.com.",
            "parameters": {
                "type": "object",
                "properties": {"meeting_type": {"type": "string", "enum": ["sales", "support"]}},
                "required": ["meeting_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_meeting",
            "description": "Book a meeting slot once caller confirms details.",
            "parameters": {
                "type": "object",
                "properties": {
                    "meeting_type": {"type": "string"},
                    "start_time": {"type": "string"},
                    "customer_name": {"type": "string"},
                    "email": {"type": "string"},
                    "topic": {"type": "string"},
                    "phone": {"type": "string"},
                },
                "required": ["meeting_type", "start_time", "customer_name", "email", "topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_booking_link",
            "description": "Email the caller a direct link to the booking page without creating a meeting.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_name": {"type": "string"},
                    "email": {"type": "string"},
                },
                "required": ["customer_name", "email"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mark_potential_lead",
            "description": "Log a warm lead who showed interest but did not commit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_name": {"type": "string"},
                    "interested_service": {"type": "string"},
                    "reason": {"type": "string"},
                    "phone": {"type": "string"},
                },
                "required": ["customer_name", "interested_service", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "transfer_to_human",
            "description": "Flag an urgent request for a human. Live transfer is not wired up yet — check transferred:false in the result.",
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
            "description": "End the call politely after saying a warm farewell.",
            "parameters": {
                "type": "object",
                "properties": {"farewell": {"type": "string"}},
                "required": ["farewell"],
            },
        },
    },
]


async def handle_tool(name: str, args: dict) -> str:
    """Execute tools and print output."""
    if name == "check_availability":
        mtype = args.get("meeting_type", "sales")
        print(f"  [TOOL] check_availability(meeting_type={mtype})")
        slots = await scheduler.get_open_slots(mtype, limit=5)
        if slots:
            print(f"  [OK] Returned {len(slots)} open slots.")
        return json.dumps({"success": True, "slots": slots})

    elif name == "book_meeting":
        email = _normalize_email(args.get("email", ""))
        print(f"  [TOOL] book_meeting(name={args.get('customer_name')}, email={email}, start_time={args.get('start_time')})")
        result = await scheduler.book_slot(
            meeting_type=args.get("meeting_type", "sales"),
            start_time=args.get("start_time", ""),
            customer_name=args.get("customer_name", "Unknown"),
            phone=args.get("phone", "+1234567890"),
            email=email,
            topic=args.get("topic", "General Discussion"),
        )
        print(f"  [BOOKING] success={result['success']}, meet_link={result.get('meet_link')}")

        if result["success"]:
            date_str, time_str = scheduler.format_display(args["start_time"])
            await email_sender.send_meeting_confirmation(
                to=email,
                customer_name=args["customer_name"],
                date=date_str,
                time_str=time_str,
                meet_link=result["meet_link"],
                meeting_type=args.get("meeting_type", "sales"),
            )
            await booking_db.log_meeting(
                meeting_type=args.get("meeting_type", "sales"),
                customer_name=args["customer_name"],
                phone=args.get("phone", "+1234567890"),
                email=email,
                topic=args.get("topic", "General Discussion"),
                date=date_str,
                time_str=time_str,
                meet_link=result["meet_link"],
                call_id="console-session",
            )
            return json.dumps({"success": True, "meet_link": result["meet_link"]})
        else:
            booking_url = os.getenv("CAL_BOOKING_URL", "")
            fallback_sent = False
            if email and booking_url:
                fb = await email_sender.send_booking_fallback_email(
                    to=email, customer_name=args["customer_name"], booking_url=booking_url
                )
                fallback_sent = fb.get("success", False)
            return json.dumps({"success": False, "error": result.get("error"), "fallback_link_sent": fallback_sent})

    elif name == "send_booking_link":
        email = _normalize_email(args.get("email", ""))
        print(f"  [TOOL] send_booking_link(name={args.get('customer_name')}, email={email})")
        booking_url = os.getenv("CAL_BOOKING_URL", "")
        res = await email_sender.send_booking_fallback_email(
            to=email, customer_name=args.get("customer_name", "Caller"), booking_url=booking_url
        )
        return json.dumps(res)

    elif name == "mark_potential_lead":
        print(f"  [TOOL] mark_potential_lead(name={args.get('customer_name')}, service={args.get('interested_service')})")
        result = await booking_db.create_lead(
            name=args.get("customer_name", "Unknown"),
            phone=args.get("phone", "+1234567890"),
            interested_service=args.get("interested_service", "General"),
            reason=args.get("reason", "Needs to check budget"),
            call_id="console-session",
        )
        return json.dumps(result)

    elif name == "transfer_to_human":
        print(f"  [TOOL] transfer_to_human(reason={args.get('reason')}) -> transferred=False (not wired up)")
        return json.dumps({"success": True, "transferred": False, "reason_logged": args.get("reason")})

    elif name == "end_call":
        farewell = args.get("farewell", "")
        print(f"  [TOOL] end_call(farewell={farewell!r}) — call ended.")
        if not farewell.strip():
            print("  [WARN] end_call called with an empty farewell — this should never happen.")
        return json.dumps({"success": True})

    return json.dumps({"error": "unknown_tool"})


async def main():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set in .env")
        return

    client = openai.AsyncOpenAI(api_key=api_key)
    model = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")

    print(f"Innoventix Hub Console Chat ready (Model: {model}). Type 'quit' to exit.\n")
    messages = [{"role": "system", "content": INNOVENTIX_SYSTEM_PROMPT}]

    while True:
        try:
            user_input = input("You: ").strip()
        except EOFError:
            break

        if not user_input:
            continue

        if user_input.lower() in ("quit", "exit"):
            break

        messages.append({"role": "user", "content": user_input})

        while True:
            resp = await client.chat.completions.create(
                model=model, messages=messages,
                tools=TOOLS_SCHEMA, tool_choice="auto",
            )
            msg = resp.choices[0].message

            if msg.tool_calls:
                messages.append(msg)
                for tc in msg.tool_calls:
                    args = json.loads(tc.function.arguments)
                    result_str = await handle_tool(tc.function.name, args)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_str,
                    })
            else:
                reply = msg.content or ""
                print(f"Bot: {reply}\n")
                messages.append({"role": "assistant", "content": reply})
                break


if __name__ == "__main__":
    asyncio.run(main())
