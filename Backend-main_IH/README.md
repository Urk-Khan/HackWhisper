# Innoventix Hub — Inbound AI Voice Agent (Backend)

An ultra-low latency, tech-enabled AI voice receptionist ("Clara") built for **Innoventix Hub** ([innoventixhub.tech](https://innoventixhub.tech)). Handles incoming phone calls, answers service and pricing inquiries, checks calendar availability, books live meetings with Google Meet links, captures warm leads, and performs live call transfers to a human team member.

Built with **Pipecat 1.8.1**, **Telnyx** (telephony), **Cartesia** (real-time STT & TTS), **OpenAI / Anthropic** (LLM reasoning), **Cal.com** (scheduling), **Resend** (email delivery), and **Supabase** (CRM & call logs).

---

## Key Features

- **Sub-Second Voice Pipeline:** Powered by Cartesia Ink-Whisper STT and Sonic TTS with Silero VAD for natural conversational pacing and responsive barge-in handling.
- **Automated Telnyx Tunneling:** Automatically spins up a `cloudflared` tunnel on launch and updates your Telnyx TeXML Application Voice URL via REST API (`auto_tunnel.py`).
- **Cal.com Scheduling:** Fetches real-time open slots across the upcoming week and books meetings directly via Cal.com API, generating Google Meet links and sending branded email confirmations.
- **Live Call Transfer:** Dynamically transfers callers to a human specialist (`TRANSFER_PHONE_NUMBER`) via Telnyx Call Control API (`transfer_to_human`), with graceful caller announcement and line bridging.
- **AI Outcome Classification:** Classifies every call in Supabase `call_logs` with high precision (`meeting_booked`, `warm_lead`, `info_inquiry`, `transferred`, `dropped_call`), preventing false positives.
- **Automated Warm Lead Capture:** Automatically captures callers who request email details, booking links, or have budget/timeline hesitations into the Supabase `leads` table.
- **Jitter-Free Call Recording:** Real-time bidirectional audio capture tap that records stutter-free, level-balanced `.wav` audio files into `call_recordings/`.

---

## Architecture Overview

```
                      ┌─────────────────────────────────────────┐
                      │          Inbound Phone Call             │
                      │         (+18555010702 / Telnyx)         │
                      └────────────────────┬────────────────────┘
                                           │
                                           ▼
                      ┌─────────────────────────────────────────┐
                      │   Cloudflare Tunnel -> FastAPI /ws      │
                      │         (Pipecat 1.8.1 Server)          │
                      └────────────────────┬────────────────────┘
                                           │
                    ┌──────────────────────┴──────────────────────┐
                    ▼                                             ▼
          ┌───────────────────┐                         ┌───────────────────┐
          │   Cartesia STT    │                         │   Cartesia TTS    │
          │   (Audio -> Text) │                         │   (Text -> Audio) │
          └─────────┬─────────┘                         └─────────▲─────────┘
                    │                                             │
                    ▼                                             │
          ┌───────────────────────────────────────────────────────┴─────────┐
          │                   LLM Reasoning (OpenAI / Anthropic)            │
          │                  Clara Receptionist System Prompt               │
          └───────────────────────────────┬─────────────────────────────────┘
                                          │
                  ┌───────────────────────┼───────────────────────┐
                  ▼                       ▼                       ▼
        ┌───────────────────┐   ┌───────────────────┐   ┌───────────────────┐
        │  Cal.com Booking  │   │   Live Transfer   │   │     Supabase      │
        │  & Email (Resend) │   │  (Telnyx Bridge)  │   │  (Leads & Logs)   │
        └───────────────────┘   └───────────────────┘   └───────────────────┘
```

---

## Supported Call Scenarios

| Scenario | Trigger / Intent | Action / Tool Used |
|---|---|---|
| **1. Info / General Q&A** | Questions about services, website, background | Answers conversationally; logs as `info_inquiry`. |
| **2. Sales & Scoping** | Inquiring about purchasing a project or pricing | Offers scoping meeting; checks availability via Cal.com. |
| **3. Meeting Booking** | Caller selects open calendar slot & confirms email | Books via `book_meeting`, logs to Supabase, sends Resend confirmation. |
| **4. Warm Lead / Busy** | Caller wants email details/link or needs to check budget | Calls `send_booking_link` / `mark_potential_lead`; logs to `leads`. |
| **5. Support Escalation** | Existing customer with unresolved technical issue | Offers support meeting via Cal.com (`meeting_type="support"`). |
| **6. Live Call Transfer** | Caller requests human agent, specialist, or urgent help | Executes `transfer_to_human` via Telnyx to `TRANSFER_PHONE_NUMBER`. |

---

## Prerequisites & Installation

### 1. Python Environment (Python 3.10 – 3.14)
```powershell
# Clone or navigate to the directory
cd Backend-main_IH

# Create virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1

# Upgrade pip & install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

### 2. Environment Configuration (`.env`)
Copy the sample environment file:
```powershell
Copy-Item .env.example .env
```

Configure your credentials in `.env`:

```ini
# ---- Telephony: Telnyx ----
TELNYX_API_KEY=KEY...
TELNYX_ACCOUNT_SID=...
TELNYX_TEXML_APP_ID=...
TELNYX_PHONE_NUMBER=+18555010702

# ---- Live Call Transfer Destination ----
TRANSFER_PHONE_NUMBER=+19177954404

# ---- STT & TTS: Cartesia ----
CARTESIA_API_KEY=sk_car_...
CARTESIA_VOICE_ID=263b9cc0-0d99-44e7-ae92-3d4ad5d2ad18

# ---- LLM: OpenAI or Anthropic ----
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-proj-...
OPENAI_MODEL=gpt-5.4-mini

# ---- Scheduling: Cal.com ----
CAL_API_KEY=cal_live_...
CAL_BOOKING_URL=https://cal.com/your-team/30min
CAL_LOOKAHEAD_DAYS=14
TIMEZONE=Asia/Karachi

# ---- Supabase: Database & CRM ----
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=sb_secret_...

# ---- Email: Resend ----
RESEND_API_KEY=re_...
RESEND_FROM_ADDRESS=Innoventix Hub <hello@innoventixhub.tech>
```

### 3. Database Setup (Supabase)
Run the SQL schema provided in [`innoventix_schema.sql`](./innoventix_schema.sql) in your Supabase SQL Editor. This sets up:
- `customers` (Pre-existing customer records for cross-sell context)
- `leads` (Warm leads captured during calls)
- `meetings` (Audit log of booked Cal.com appointments)
- `call_logs` (Full call transcripts, durations, and classified outcomes)

---

## Running the Bot

### One-Command Startup
```powershell
python bot.py
```
*(Or double-click `start_bot.bat`)*

**What this command does automatically:**
1. Spawns `cloudflared` to establish a secure public HTTPS/WSS tunnel.
2. Updates your Telnyx TeXML Application's Voice URL to point to the active tunnel.
3. Pre-warms the Silero VAD and Cartesia WebSocket connection path.
4. Starts the Pipecat WebSocket server on `http://localhost:7860/ws`.

---

## Testing & Verification

### 1. Interactive Terminal Chat
Test conversational responses and prompts in your console without placing a phone call:
```powershell
python test_console_chat.py
```

### 2. Automated Scenario Test Suite
Run end-to-end simulated scenarios (Sales booking, Warm lead, Q&A, Escalation, Transfer):
```powershell
python test_scenario.py
```

### 3. Audio Recording Debugger
Inspect audio streams, sample rates, and mixer outputs:
```powershell
python audio_debug.py
```

---

## Project Structure

```text
Backend-main_IH/
├── bot.py                     # Main Pipecat voice pipeline & server entry point
├── tools.py                   # LLM tools (check_availability, book_meeting, transfer_to_human, etc.)
├── prompts.py                 # Clara system prompt, identity, guidelines & dynamic time injection
├── booking_db.py              # Supabase REST client, lead capture & AI outcome classifier
├── cal_com.py                 # Cal.com v2 REST API client for slot lookup and bookings
├── auto_tunnel.py             # Automated Cloudflare tunnel & Telnyx webhook updater
├── call_recorder.py           # Jitter-free bidirectional 8kHz audio capture tap
├── email_sender.py            # Resend email client for confirmations & booking links
├── demo_display.py            # Real-time formatted terminal UI logger
├── latency_logger.py          # Per-turn latency breakdown logger (STT, LLM, TTS)
├── warmup.py                  # Model & connection pre-warming
├── innoventix_schema.sql      # Supabase database DDL schema
├── test_console_chat.py       # Console text chat test utility
├── test_scenario.py           # End-to-end scenario test runner
├── requirements.txt           # Python dependencies
├── start_bot.bat              # Windows batch launcher
├── .env.example               # Template environment configuration
└── README.md                  # Documentation
```

---

## License

Proprietary — Built exclusively for **Innoventix Hub** ([innoventixhub.tech](https://innoventixhub.tech)).
