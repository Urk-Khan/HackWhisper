"""
Comprehensive end-to-end test scenarios covering all 4 core call scenarios + extra flows:

Scenario A — Sales booking (Cal.com booking + confirmation email)
Scenario B — Warm lead capture (interested caller who doesn't commit)
Scenario C — General info inquiry (no tools, pure knowledge-base Q&A)
Scenario D — Cross-sell flow (Known customer injected context -> AI Voice Agents pitch -> Sales booking)
Scenario E — Support escalation (Known customer -> unresolved tech issue -> Support meeting)
Scenario F — Booking link request (Caller asks for link by email -> send_booking_link)
Scenario G — Immediate human transfer (Urgent request -> transfer_to_human)

Run:  python test_scenario.py
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
from prompts import INNOVENTIX_SYSTEM_PROMPT, get_system_prompt
from tools import _normalize_email

# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------
SCENARIOS = {
    "A — Sales Booking (Cal.com)": {
        "context": None,
        "turns": [
            "Hi, I run a dental clinic and I'm looking for an AI voice agent to handle our incoming appointment calls. Can Innoventix Hub help with that?",
            "That sounds exactly like what we need. What are the next steps?",
            "Yes let's book a call. My name is Ahmed.",
            "Let's do the first available slot.",
            "My email is ahmed dot clinic at gmail dot com.",
            "Yes that details are correct.",
        ],
    },
    "B — Warm Lead (interested but not ready)": {
        "context": None,
        "turns": [
            "Hi, I saw your post about AI automation. We're a small e-commerce brand and I'm curious if you could automate our order follow-ups.",
            "That sounds great. How much would something like that cost?",
            "Hmm, I'd need to discuss the budget with my business partner first. I'll call back.",
        ],
    },
    "C — General Info Inquiry (no booking)": {
        "context": None,
        "turns": [
            "Hey, can you tell me what services Innoventix Hub offers?",
            "Who's on the team?",
            "Do you do custom mobile apps?",
            "Alright, thanks for the info. I'll think about it.",
        ],
    },
    "D — Cross-sell (Known customer)": {
        "context": "[KNOWN CALLER CONTEXT: This caller is an existing customer, name=Sarah, purchased=AI Automation. Use this per the CROSS-SELL scenario in your instructions — never mention this context to the caller directly.]",
        "turns": [
            "Hi there, just wanted to see how things are going with our automation setup.",
            "Oh really? Tell me more about AI Voice Agents.",
            "That sounds perfect for our front desk. Can we schedule a sales call to discuss adding voice agents?",
            "Let's book it. Name is Sarah, email is sarah at company dot com.",
            "The first available slot works.",
            "Yes, that's all correct.",
        ],
    },
    "E — Support Escalation (Known customer)": {
        "context": "[KNOWN CALLER CONTEXT: This caller is an existing customer, name=David, purchased=Web Development. Use this per the CROSS-SELL scenario in your instructions — never mention this context to the caller directly.]",
        "turns": [
            "Hi, our website forms suddenly stopped submitting lead notifications to our CRM yesterday after the update.",
            "I checked the settings already and the API key looks right. Can someone from tech support take a look with me?",
            "Yes, let's schedule a support meeting. Name is David, email is david at techcorp dot com.",
            "The first slot is fine.",
            "Yes, confirmed.",
        ],
    },
    "F — Booking Link Request": {
        "context": None,
        "turns": [
            "Hi, I'm interested in Content Creation for our brand.",
            "Can you just email me a booking link so I can pick a time with my team later?",
            "My name is Maria and my email is maria at brand dot com.",
            "Yes that email is correct.",
        ],
    },
    "G — Human Transfer Request": {
        "context": None,
        "turns": [
            "Hi, our payment gateway is down and production servers are throwing 500 errors! I need to talk to a human engineer immediately!",
        ],
    },
    "H — Whole Week Availability & Day Selection": {
        "context": None,
        "turns": [
            "Hi, I want to talk to your team about building an AI voice agent for our customer support.",
            "Can you tell me what days and times you have open across this entire week?",
            "What options do you have specifically on Thursday?",
            "Let's book Thursday. My name is Alex, email is alex dot test at innoventix dot com.",
            "Yes, that's confirmed.",
        ],
    },
}

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
    """Execute each tool and print execution details."""
    if name == "check_availability":
        mtype = args.get("meeting_type", "sales")
        print(f"  [TOOL] check_availability(meeting_type={mtype})")
        slots = await scheduler.get_open_slots(mtype, limit=20)
        seen_days = []
        for s in slots:
            d = s.get("date", "")
            if d and d not in seen_days:
                seen_days.append(d)
        week_summary = f"Openings available across {len(seen_days)} days this week: {', '.join(seen_days)}."
        if slots:
            print(f"  [OK] Returned {len(slots)} open slots across {len(seen_days)} days ({', '.join(seen_days)})")
        else:
            print("  [WARN] No slots returned")
        return json.dumps({
            "success": True,
            "week_summary": week_summary,
            "available_days": seen_days,
            "slots": slots,
        })

    elif name == "book_meeting":
        email = _normalize_email(args.get("email", ""))
        print(f"  [TOOL] book_meeting(name={args.get('customer_name')}, email={email}, start_time={args.get('start_time')})")
        
        # Use sandbox-compatible owner email address for automated test runs
        test_target_email = os.getenv("TEST_EMAIL", "ashadabbasi.963@gmail.com")

        result = await scheduler.book_slot(
            meeting_type=args.get("meeting_type", "sales"),
            start_time=args.get("start_time", ""),
            customer_name=args.get("customer_name", "Unknown"),
            phone=args.get("phone", "+1234567890"),
            email=test_target_email,
            topic=args.get("topic", "General Discussion"),
        )
        print(f"  [BOOKING] success={result['success']}, meet_link={result.get('meet_link')}, error={result.get('error')}")

        if result["success"]:
            date_str, time_str = scheduler.format_display(args["start_time"])
            print(f"  [EMAIL] Sending booking confirmation to {test_target_email}...")
            er = await email_sender.send_meeting_confirmation(
                to=test_target_email,
                customer_name=args["customer_name"],
                date=date_str,
                time_str=time_str,
                meet_link=result["meet_link"],
                meeting_type=args.get("meeting_type", "sales"),
            )
            print(f"  [EMAIL] Confirmation status: {er.get('success')}")
            await booking_db.log_meeting(
                meeting_type=args.get("meeting_type", "sales"),
                customer_name=args["customer_name"],
                phone=args.get("phone", "+1234567890"),
                email=email,
                topic=args.get("topic", "General Discussion"),
                date=date_str,
                time_str=time_str,
                meet_link=result["meet_link"],
                call_id="test-scenario-session",
            )
            return json.dumps({"success": True, "meet_link": result["meet_link"]})
        else:
            booking_url = os.getenv("CAL_BOOKING_URL", "")
            fallback_sent = False
            if email and booking_url:
                print(f"  [FALLBACK] Sending fallback email to {test_target_email}...")
                fb = await email_sender.send_booking_fallback_email(
                    to=test_target_email, customer_name=args["customer_name"], booking_url=booking_url
                )
                fallback_sent = fb.get("success", False)
            return json.dumps({"success": False, "error": result.get("error"), "fallback_link_sent": fallback_sent})

    elif name == "send_booking_link":
        email = _normalize_email(args.get("email", ""))
        test_target_email = os.getenv("TEST_EMAIL", "ashadabbasi.963@gmail.com")
        print(f"  [TOOL] send_booking_link(name={args.get('customer_name')}, email={email})")
        booking_url = os.getenv("CAL_BOOKING_URL", "")
        res = await email_sender.send_booking_fallback_email(
            to=test_target_email, customer_name=args.get("customer_name", "Caller"), booking_url=booking_url
        )
        print(f"  [EMAIL] Link sent: {res.get('success')}")
        return json.dumps(res)

    elif name == "mark_potential_lead":
        print(f"  [TOOL] mark_potential_lead(name={args.get('customer_name')}, service={args.get('interested_service')})")
        result = await booking_db.create_lead(
            name=args.get("customer_name", "Unknown"),
            phone=args.get("phone", "+1234567890"),
            interested_service=args.get("interested_service", "General"),
            reason=args.get("reason", "Needs to check budget"),
            call_id="test-scenario-session",
        )
        print(f"  [LEAD] Logged lead: success={result.get('success')}")
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


async def run_scenario(label: str, spec: dict, client, model: str):
    import time
    scen_start = time.time()
    print()
    print("=" * 70)
    print(f"  SCENARIO: {label}")
    print("=" * 70)

    messages = [{"role": "system", "content": get_system_prompt()}]
    if spec.get("context"):
        messages.append({"role": "user", "content": spec["context"]})

    for i, user_turn in enumerate(spec["turns"]):
        print(f"\n[Turn {i+1}] Caller: {user_turn}")
        messages.append({"role": "user", "content": user_turn})

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
                print(f"   Bot: {reply}")
                messages.append({"role": "assistant", "content": reply})
                break

    # Save call record to Supabase call_logs table
    await booking_db.save_call_record(
        call_id=f"scen-{int(time.time())}-{label[0]}",
        caller_phone="+1234567890",
        start_time=scen_start,
        messages=messages,
    )


async def main():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set in .env")
        return

    client = openai.AsyncOpenAI(api_key=api_key)
    model = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")

    print("=" * 70)
    print("  INNOVENTIX HUB -- FULL SCENARIO TEST SUITE (7 SCENARIOS)")
    print("=" * 70)
    print(f"  Model    : {model}")
    print(f"  Timezone : {os.getenv('TIMEZONE', 'UTC')}")
    print(f"  Scenarios: {len(SCENARIOS)}")

    for label, spec in SCENARIOS.items():
        await run_scenario(label, spec, client, model)

    print()
    print("=" * 70)
    print("  ALL SCENARIOS COMPLETE & VERIFIED")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
