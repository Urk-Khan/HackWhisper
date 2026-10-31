# HackWhisper — AI Hacktoberfest Voice Calling Agent

[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat&logo=python&logoColor=white)](https://www.python.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Database-336791?style=flat&logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Telnyx](https://img.shields.io/badge/Telnyx-TeXML_Voice_%26_SMS-00E599?style=flat)](https://telnyx.com/)
[![Cartesia](https://img.shields.io/badge/Cartesia-Ultra_Low_Latency_Voice-7C3AED?style=flat)](https://cartesia.ai/)
[![Hacktoberfest](https://img.shields.io/badge/Hacktoberfest-Events_API-FF7A59?style=flat)](https://hacktoberfest.com/)

A real-time AI voice calling agent that provides callers with information about Hacktoberfest events, answers questions, and on request delivers event details and registration links via **Email** or **SMS**.

---

## 🎯 Main Features

1. **Inbound Voice Calling:** Users call the AI agent over phone lines (via Telnyx or SignalWire).
2. **Event Q&A:** Answers questions about Hacktoberfest rules, rewards, eligibility, and schedules.
3. **City-Wise Event Search:** Discovers local and virtual events by city, region, or format.
4. **Registration Delivery:** Sends official event links directly via **SMS text** or **Email** upon request.
5. **Automatic Event Management (Daily Full Fetch + Upsert + Historical Versioning):**
   - Automatically syncs every 24 hours from the official [Hacktoberfest MLH Events API](https://hacktoberfest-api.mlh.com/api/events).
   - Upserts new events into PostgreSQL.
   - Archives previous event states in an `event_history` table on update (never deletes older events).
   - Records execution audit trails in `event_sync_logs`.
6. **AI Response Method:** Structured database queries and GPT Function Calling (`search_events`, `get_event_details`, `get_registration_link`, `send_event_email`, `send_event_sms`).

---

## ⚡ System Architecture

```mermaid
flowchart LR
    Caller[Inbound Caller<br/>+18555010702] -->|Phone Call| Telnyx[Telnyx TeXML Voice]
    Telnyx <-->|WebSocket Audio| Bot[Pipecat AI Agent<br/>Backend-main_IH]
    Bot <--> Cartesia[Cartesia STT & TTS]
    Bot <--> LLM[OpenAI GPT-4o-mini]
    Bot -->|SMS Registration Link| SMS[Telnyx SMS API]
    Bot -->|Email Event Details| Email[Resend API]
    Bot -->|Daily Sync & Upsert| API[Hacktoberfest Events API]
    Bot <-->|Query & History| DB[(PostgreSQL / Supabase)]
    Bot -->|Live Transfer| Human[Event Organizer<br/>+19177954404]
```

---

## 🚀 Getting Started

### Backend Execution
```powershell
cd Backend-main_IH
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python bot.py
```

---

## 💾 Hardware & RAM Requirements

- **Minimum:** **1 GB to 2 GB RAM** (Runs on basic cloud droplets/VPS).
- **Recommended for Production:** **2 GB to 4 GB RAM** (Accommodates concurrent calls, background daily sync, and audio pipeline with low latency).
