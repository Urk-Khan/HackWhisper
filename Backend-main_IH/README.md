# HackWhisper — AI Hacktoberfest Voice Calling Agent (Backend)

An ultra-low latency, real-time AI voice receptionist ("Whisper") built for **Hacktoberfest** event inquiries, discovery, and attendee support. Handles incoming phone calls, answers questions regarding Hacktoberfest rules and rewards, searches city-wise and regional hack days, and sends event details + registration links directly to attendees via **SMS** or **Email**.

Built with **Pipecat 1.8.1**, **Telnyx** (telephony & SMS), **Cartesia** (real-time STT & TTS), **OpenAI / Anthropic** (GPT function calling), **PostgreSQL / Supabase** (event management with daily upsert & historical versioning), and **Resend** (email delivery).

---

## ⚡ Key Features

- **Sub-Second Voice Pipeline:** Powered by Cartesia Ink-Whisper STT and Sonic TTS with Silero VAD for natural conversational pacing and responsive barge-in handling.
- **Automated Hacktoberfest Event Management:**
  - **Daily Full Fetch:** Fetches all events from the official MLH Hacktoberfest Events API (`https://hacktoberfest-api.mlh.com/api/events`) every 24 hours automatically.
  - **Upsert + Historical Versioning:** Adds new events with `version = 1`. If an event changes, archives the prior version in `event_history` table and updates `events` with `version + 1`. Past/ended events are never deleted.
  - **Execution Audit Trail:** Tracks sync results, counts, and errors in `event_sync_logs`.
- **AI Response Method (GPT Function Calling):**
  - `search_events(city)`: Discovers events city-wise.
  - `get_event_details(event_id)`: Provides schedules, venues, and descriptions.
  - `get_registration_link(event_id)`: Retrieves the registration link and offers delivery via SMS or Email.
  - `send_event_email(email, event_id)`: Dispatches branded email with registration link via Resend.
  - `send_event_sms(phone_number, event_id)`: Texts event details & direct link via Telnyx SMS API.
- **Automated Cloudflare Tunneling:** One-click startup with automatic TeXML webhook registration (`auto_tunnel.py`).
- **Live Organizer Transfer:** Dynamically bridges callers to an organizer or human specialist (`TRANSFER_PHONE_NUMBER`) via Telnyx Call Control API (`transfer_to_human`).

---

## 🏗️ System Architecture & Call Flow

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
          │                  Whisper Voice Agent System Prompt              │
          └───────────────────────────────┬─────────────────────────────────┘
                                          │
                  ┌───────────────────────┼───────────────────────┐
                  ▼                       ▼                       ▼
        ┌───────────────────┐   ┌───────────────────┐   ┌───────────────────┐
        │  Search & Details │   │   SMS & Email     │   │   PostgreSQL DB   │
        │ (MLH Events API)  │   │ (Telnyx / Resend) │   │ (Events, Version) │
        └───────────────────┘   └───────────────────┘   └───────────────────┘
```

---

## 🚀 Quick Start Guide

### 1. Environment Setup
```powershell
cd Backend-main_IH
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

### 2. Configure `.env`
Add your API keys for:
- `TELNYX_API_KEY`, `TELNYX_PHONE_NUMBER`
- `CARTESIA_API_KEY`
- `OPENAI_API_KEY`
- `DATABASE_URL` (PostgreSQL connection string)
- `RESEND_API_KEY` (for sending event confirmation emails)

### 3. Run the Bot
```powershell
python bot.py
```
This automatically boots the Cloudflare tunnel, updates Telnyx, syncs Hacktoberfest events into the database, pre-warms the audio pipeline, and starts answering incoming phone calls.

---

## 💾 Hardware & RAM Requirements

| Component | Minimum RAM | Recommended RAM |
|---|---|---|
| Python Pipecat 1.8.1 Voice Runtime | ~300 MB | 512 MB |
| Silero VAD + Smart Turn ONNX models | ~250 MB | 500 MB |
| In-Memory Hacktoberfest Events Cache (500+ events) | ~15 MB | 30 MB |
| Asyncpg PostgreSQL Connection Pool | ~40 MB | 100 MB |
| **Total System RAM** | **1 GB - 1.5 GB** | **2 GB - 4 GB** |

- **Minimum:** **1 GB to 2 GB RAM** (Runs smoothly on a basic VPS, e.g. AWS t4g.small, DigitalOcean $6/mo droplet, or local machine).
- **Recommended for Production (with concurrency):** **2 GB to 4 GB RAM** to comfortably handle concurrent phone calls and background daily sync tasks.
