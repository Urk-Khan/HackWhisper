# PROJECT BRIEFING — Innoventix Hub Inbound AI Voice Agent
*(Give this file to a new chat to resume work with full context.)*

---

## WHAT THIS IS

An inbound phone-answering AI voice agent for **Innoventix Hub**, a tech-enabled production house with five integrated service disciplines:
1. **Content Creation** (Video Editing, YouTube Automation, AI UGC, Portfolio)
2. **AI Automation** (SMB Solutions, Custom CRM, GHL Integration, n8n, Projects Portfolio)
3. **AI Voice Agents** (Inbound & Outbound Voice AI, Custom Integrations, Demos)
4. **Web Development** (Custom React/Node/TypeScript, WordPress, Web Design & UX)
5. **SEO (Search Engine Optimization)** (SEO Audits & Strategy, Technical SEO, Content Optimization, Ongoing Management)

When someone calls the Telnyx phone number, the bot answers, conducts a natural voice conversation, and handles four main scenarios:
- **Scenario 1 — Info / General Inquiries**: Direct answers from the knowledge base in the prompt.
- **Scenario 2 — Sales / Purchase Interest**: Checks slot availability and books a discovery meeting.
- **Scenario 3 — Cross-sell**: If an incoming phone matches an existing customer in Supabase, the agent greets them by name, asks about their current service, and politely pitches a complementary service.
- **Scenario 4 — Support Escalation**: For unresolved client support issues, schedules a technical support meeting with a specific topic logged.
- **Outcome — Warm Lead Tracking**: If a caller shows buying signals but doesn't book immediately, logs a lead in Supabase with summary and next steps.

---

## CURRENT ARCHITECTURE & TECH STACK

```
Caller's Phone
      │
      ▼
   Telnyx (TeXML) ──► Cloudflare Tunnel (auto_tunnel.py) ──► bot.py (FastAPI / WebSocket)
                                                                  │
                                           ┌──────────────────────┴──────────────────────┐
                                           │ Supabase: booking_db.get_customer_by_phone()│
                                           │ (Checks caller ID before greeting)          │
                                           └──────────────────────┬──────────────────────┘
                                                                  ▼
                                                       Pipecat Voice Pipeline:
                                                       - Cartesia STT
                                                       - LLM (OpenAI gpt-4o / Anthropic)
                                                       - Cartesia TTS
                                                                  │
                                                                  ▼
                                                       Tool Calling (tools.py):
                                           ┌──────────────────────┼──────────────────────┐
                                           ▼                      ▼                      ▼
                                      Cal.com API             Supabase DB           Call Controls
                                    (cal_com.py)            (booking_db.py)       - transfer_to_human
                                    - check_availability    - leads table         - end_call
                                    - book_meeting          - bookings table
                                    - video link & email    - customers table
```

### Core Components

| Component | Provider / Tech | Status / Role |
|---|---|---|
| **Telephony** | Telnyx (TeXML Webhook) | Inbound phone routing via SIP/TeXML |
| **Tunneling** | Cloudflare Tunnel (`auto_tunnel.py`) | Public URL pointing to local port `8765`, updates Telnyx TeXML URL |
| **Orchestration** | Pipecat AI framework (`bot.py`) | Audio frames, pipeline flow, interrupts, latency logging |
| **Voice / TTS** | Cartesia (`sonic-multilingual` / `sonic-english`) | Ultra-low latency voice synthesis |
| **Transcriber / STT** | Cartesia / Deepgram | Speech-to-text |
| **Meeting Scheduler** | **Cal.com v2 API** (`cal_com.py`) | **ACTIVE**: Slot availability (`/slots/available`), bookings (`/bookings`), handles video call link and attendee emails automatically |
| **Database** | **Supabase** (`booking_db.py`) | **ACTIVE**: Customer cross-sell lookup, warm lead capture, booking audit trail |
| **Google Suite (Sheets/Cal)**| `google_sheets.py` & `google_calendar.py` | **PARKED / PRESERVED**: Kept for future migration when Google Cloud setup is complete |
| **Direct Email (Resend)** | `email_sender.py` | **PARKED**: Cal.com handles booking confirmation emails directly for now |

---

## KEY DESIGN RULES & RECENT FIXES

1. **Caller Phone Number Auto-Injection**:
   - The agent **never asks the caller for their phone number**.
   - `prompts.py` explicitly forbids asking for phone number because inbound caller ID is already captured.
   - In `tools.py`, both `book_meeting` and `mark_potential_lead` automatically inject the caller's phone from `params.app_resources.get("caller_phone")` or `session_id`.

2. **Video Meeting Links**:
   - Meeting links are generated directly by Cal.com (or Google Meet if migrated). The prompt instructs the bot to refer to them as "video call link" or "meeting confirmation link".

3. **Pre-Greeting Customer Identification**:
   - `bot.py` queries Supabase `customers` table using caller ID before generating the greeting.
   - If found, passes `customer_info` into the system prompt so the agent immediately greets them by name and knows their past services.

4. **Cal.com Event Types**:
   - `CAL_EVENT_TYPE_SALES_ID`: Used for Sales / Discovery bookings.
   - `CAL_EVENT_TYPE_SUPPORT_ID`: Used for Support Escalation bookings.

---

## PROJECT DIRECTORY & IMPORTANT FILES

```
innoventix-voice-agent/
├── bot.py                  # Main Pipecat bot runner & WebSocket server
├── prompts.py              # System prompt, scenarios, knowledge base, anti-hallucination rules
├── tools.py                # LLM function tools (check_availability, book_meeting, mark_potential_lead, etc.)
├── cal_com.py              # Cal.com API v2 client (slots, bookings)
├── booking_db.py           # Supabase REST client for customers, leads, and bookings
├── auto_tunnel.py          # Auto-starts Cloudflare tunnel & updates Telnyx TeXML webhook
├── warmup.py               # Pre-initializes STT/TTS connections to reduce first-turn latency
├── latency_logger.py       # Metrics logger for turn latency (STT, LLM, TTS)
├── google_sheets.py        # (Parked) Google Sheets slot management
├── google_calendar.py      # (Parked) Google Calendar freeBusy + event creation
├── email_sender.py         # (Parked) Resend email confirmation
├── innoventix_schema.sql   # Supabase SQL schema definitions
├── .env                    # Environment variables & API keys
└── PROJECT_BRIEF.md        # This brief
```

---

## SETUP & ENVIRONMENT VARIABLES (`.env`)

Essential keys in `.env`:
```env
# Telephony
TELNYX_API_KEY=KEY...
TELNYX_CONNECTION_ID=...
TELNYX_PHONE_NUMBER=+1...
TELNYX_TEXML_APP_ID=...

# AI & Voice
OPENAI_API_KEY=sk-...
CARTESIA_API_KEY=...

# Cal.com Integration (Active)
CAL_API_KEY=cal_live_...
CAL_EVENT_TYPE_SALES_ID=...
CAL_EVENT_TYPE_SUPPORT_ID=...

# Supabase (Active)
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_SERVICE_KEY=sb_secret_... # or service_role key

# Parked Credentials (when migrating back to Google Cloud)
# GOOGLE_SERVICE_ACCOUNT_JSON=...
# GOOGLE_SPREADSHEET_ID=...
# RESEND_API_KEY=...
```

---

## IMMEDIATE NEXT STEPS TO RUN

1. **Cal.com Availability Configuration**:
   - In Cal.com dashboard (`app.cal.com`), ensure the **Default Schedule** has active hours (e.g., Monday–Friday, 9:00 AM – 5:00 PM).
   - Ensure both the **Sales Meeting** and **Support Meeting** event types are attached to this schedule so that `check_availability` returns open slots.
2. **Restart the Bot**:
   - Kill any lingering process (`Ctrl+C` in terminal).
   - Run:
     ```powershell
     python bot.py
     ```
3. **Inbound Test Call**:
   - Call the Telnyx phone number.
   - Say: *"Hi, I'm interested in an AI voice agent for my business."*
   - Test slot fetching, booking confirmation, and warm lead capture.
