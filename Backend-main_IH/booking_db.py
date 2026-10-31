"""
Direct PostgreSQL integration for the Innoventix bot's CRM-lite needs: knowing who's
calling (for cross-sell), tracking warm leads who didn't book, and logging meetings for
reporting. Connects directly to local PostgreSQL (via DATABASE_URL or POSTGRES_URL)
using asyncpg connection pooling.

Four tables (auto-created on initialization if not exists):
  customers        — pre-populated externally when a deal closes; this bot only READS it
                      (via get_customer_by_phone) to know purchase history for cross-sell.
  leads            — the bot writes here (mark_potential_lead in tools.py) whenever a
                      caller shows real interest but doesn't commit.
  meetings         — an audit-trail log of every booked meeting (both sales and support).
  call_logs        — call transcript, duration, and classified outcome.
"""

import asyncio
import os
import aiohttp
from loguru import logger

try:
    import asyncpg
except ImportError:
    asyncpg = None

_pool: "asyncpg.Pool | None" = None
_pool_lock = asyncio.Lock()

INIT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS customers (
  id BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
  phone TEXT UNIQUE NOT NULL,
  name TEXT,
  email TEXT,
  purchased_services TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS leads (
  id BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
  name TEXT,
  phone TEXT,
  interested_service TEXT,
  reason TEXT,
  status TEXT DEFAULT 'warm',
  call_id TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS meetings (
  id BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
  meeting_type TEXT,
  customer_name TEXT,
  phone TEXT,
  email TEXT,
  topic TEXT,
  date TEXT,
  time TEXT,
  meet_link TEXT,
  call_id TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS call_logs (
  id BIGINT PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
  call_id TEXT,
  caller_phone TEXT,
  duration_seconds INTEGER,
  transcript TEXT,
  outcome TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
"""


def _get_db_url() -> str:
    """Returns database connection URL from environment variables."""
    url = (
        os.getenv("DATABASE_URL")
        or os.getenv("POSTGRES_URL")
        or os.getenv("POSTGRESQL_URL")
        or ""
    ).strip()
    # Normalize postgres:// to postgresql:// for asyncpg
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    return url


def _get_supabase_config() -> tuple[str, str]:
    """Fallback if SUPABASE_URL and SUPABASE_SERVICE_KEY are provided."""
    return os.getenv("SUPABASE_URL", "").rstrip("/"), os.getenv("SUPABASE_SERVICE_KEY", "")


async def _get_pool() -> "asyncpg.Pool | None":
    global _pool
    if _pool and not _pool._closed:
        return _pool

    if asyncpg is None:
        logger.warning("asyncpg is not installed; PostgreSQL operations unavailable")
        return None

    db_url = _get_db_url()
    if not db_url:
        return None

    async with _pool_lock:
        if _pool and not _pool._closed:
            return _pool
        try:
            pool = await asyncpg.create_pool(
                dsn=db_url,
                min_size=1,
                max_size=10,
                command_timeout=6,
            )
            # Initialize tables if not already created
            async with pool.acquire() as conn:
                await conn.execute(INIT_SCHEMA_SQL)
            _pool = pool
            logger.info("Successfully connected to PostgreSQL database and initialized schema")
            return _pool
        except Exception as e:
            logger.error(f"Failed to connect to PostgreSQL database: {e}")
            return None


def _headers(service_key: str, prefer: str = "return=representation") -> dict:
    return {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        "Prefer": prefer,
    }


async def get_customer_by_phone(phone: str) -> dict | None:
    """Looks up a customer by phone number for the pre-greeting context injection in
    bot.py. Returns None if not configured, not found, or on any error — bot.py treats
    None as 'unknown caller' and proceeds with a plain greeting, so this fails safe."""
    if not phone:
        return None

    pool = await _get_pool()
    if pool:
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT id, phone, name, email, purchased_services, created_at FROM customers WHERE phone = $1 LIMIT 1",
                    phone,
                )
                if row:
                    return dict(row)
                return None
        except Exception as e:
            logger.error(f"PostgreSQL customer lookup failed: {e}")
            return None

    # Fallback to Supabase REST if configured
    supabase_url, supabase_key = _get_supabase_config()
    if supabase_url and supabase_key:
        url = f"{supabase_url}/rest/v1/customers?phone=eq.{phone}&select=*"
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6)) as session:
                async with session.get(url, headers=_headers(supabase_key)) as resp:
                    if resp.status >= 400:
                        logger.error(f"Customer lookup failed ({resp.status}): {await resp.text()}")
                        return None
                    rows = await resp.json()
                    return rows[0] if rows else None
        except Exception as e:
            logger.error(f"Customer lookup request failed: {e}")
            return None

    return None


async def create_lead(
    *,
    name: str,
    phone: str,
    interested_service: str,
    reason: str,
    call_id: str | None,
) -> dict:
    """Logs a warm lead — a caller who showed real interest but didn't book. Returns
    {"success": bool, "error": str | None}."""
    pool = await _get_pool()
    if pool:
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO leads (name, phone, interested_service, reason, status, call_id)
                    VALUES ($1, $2, $3, $4, 'warm', $5)
                    """,
                    name,
                    phone,
                    interested_service,
                    reason,
                    call_id,
                )
                logger.info(f"Logged warm lead in PostgreSQL: {name} ({phone}) — interested in {interested_service}")
                return {"success": True, "error": None}
        except Exception as e:
            logger.error(f"PostgreSQL lead insert failed: {e}")
            return {"success": False, "error": "db_insert_failed"}

    # Fallback to Supabase REST if configured
    supabase_url, supabase_key = _get_supabase_config()
    if not supabase_url or not supabase_key:
        logger.error("DATABASE_URL (or SUPABASE_URL/SUPABASE_SERVICE_KEY) not set in environment")
        return {"success": False, "error": "db_not_configured"}

    payload = {
        "name": name,
        "phone": phone,
        "interested_service": interested_service,
        "reason": reason,
        "status": "warm",
        "call_id": call_id,
    }

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6)) as session:
            async with session.post(
                f"{supabase_url}/rest/v1/leads",
                json=payload,
                headers=_headers(supabase_key, prefer="return=minimal"),
            ) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    logger.error(f"Lead insert failed ({resp.status}): {body}")
                    return {"success": False, "error": "db_insert_failed"}
    except Exception as e:
        logger.error(f"Lead insert request failed: {e}")
        return {"success": False, "error": "db_unreachable"}

    logger.info(f"Logged warm lead: {name} ({phone}) — interested in {interested_service}")
    return {"success": True, "error": None}


async def log_meeting(
    *,
    meeting_type: str,
    customer_name: str,
    phone: str,
    email: str,
    topic: str,
    date: str,
    time_str: str,
    meet_link: str | None,
    call_id: str | None,
) -> None:
    """Fire-and-forget audit log of a successfully booked meeting. Never raises — Cal.com is
    the real booking; this is reporting only, so a logging failure here should never surface
    as a booking failure to the caller."""
    pool = await _get_pool()
    if pool:
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO meetings (meeting_type, customer_name, phone, email, topic, date, time, meet_link, call_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    """,
                    meeting_type,
                    customer_name,
                    phone,
                    email,
                    topic,
                    date,
                    time_str,
                    meet_link,
                    call_id,
                )
                logger.info(f"Logged meeting in PostgreSQL: {customer_name} ({date} at {time_str})")
                return
        except Exception as e:
            logger.warning(f"PostgreSQL meeting log insert failed: {e}")
            return

    # Fallback to Supabase REST if configured
    supabase_url, supabase_key = _get_supabase_config()
    if not supabase_url or not supabase_key:
        logger.error("DATABASE_URL or Supabase credentials not set — meeting not logged")
        return

    payload = {
        "meeting_type": meeting_type,
        "customer_name": customer_name,
        "phone": phone,
        "email": email,
        "topic": topic,
        "date": date,
        "time": time_str,
        "meet_link": meet_link,
        "call_id": call_id,
    }

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6)) as session:
            async with session.post(
                f"{supabase_url}/rest/v1/meetings",
                json=payload,
                headers=_headers(supabase_key, prefer="return=minimal"),
            ) as resp:
                if resp.status >= 400:
                    logger.warning(f"Meeting log insert failed ({resp.status}): {await resp.text()}")
    except Exception as e:
        logger.warning(f"Meeting log request failed: {e}")


async def _classify_transcript_with_ai(transcript: str) -> str:
    """Uses LLM to classify non-meeting call outcomes with high precision."""
    openai_key = os.getenv("OPENAI_API_KEY", "")
    if not openai_key or not transcript.strip():
        return ""

    prompt = f"""You are an outcome classifier for an inbound AI receptionist voice agent.
Analyze the conversation transcript and classify the outcome into EXACTLY one of these labels:
- "warm_lead": The caller showed purchase interest, requested an email with details/rates/link, asked for a callback or quote, or is busy right now but wants follow-up, but did NOT book a live calendar slot.
- "info_inquiry": The caller asked general questions about services, pricing, company background, website, or declined to book/said they will visit in person.
- "transferred": The caller requested to speak to a person/agent and was transferred.

Transcript:
{transcript}

Return ONLY the single word label: warm_lead, info_inquiry, or transferred."""

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=openai_key)
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_completion_tokens=10,
            ),
            timeout=4.0,
        )
        res = resp.choices[0].message.content.strip().lower().replace('"', '').replace("'", "")
        if res in ("warm_lead", "info_inquiry", "transferred"):
            return res
    except Exception as e:
        logger.debug(f"AI classification failed or timed out: {e}")
    return ""


async def save_call_record(
    *,
    call_id: str | None,
    caller_phone: str | None,
    start_time: float,
    messages: list[dict],
    explicit_outcome: str | None = None,
) -> bool:
    """Fire-and-forget call log: extracts transcript, classifies outcome, and inserts into
    the call_logs table. Never raises — a logging failure must never surface as a call failure."""
    # Build clean dialog transcript
    lines = []
    for msg in messages:
        if isinstance(msg, dict):
            role = msg.get("role", "")
            raw_content = msg.get("content")
        else:
            role = getattr(msg, "role", "")
            raw_content = getattr(msg, "content", "")

        if raw_content is None:
            continue
        content = str(raw_content).strip()
        if not content or content == "None":
            continue
        if content.startswith("[") and content.endswith("]"):
            continue
        if role == "user":
            lines.append(f"Caller: {content}")
        elif role == "assistant":
            lines.append(f"Agent: {content}")

    transcript = "\n".join(lines)
    duration = max(1, int(__import__("time").time() - start_time))

    outcome = explicit_outcome
    pool = await _get_pool()

    if pool:
        try:
            async with pool.acquire() as conn:
                # 1. Did this call_id create a meeting?
                if outcome != "meeting_booked" and call_id:
                    m_row = await conn.fetchrow("SELECT id FROM meetings WHERE call_id = $1 LIMIT 1", call_id)
                    if m_row:
                        outcome = "meeting_booked"

                # 2. Check if already logged as warm lead
                if not outcome and call_id:
                    l_row = await conn.fetchrow("SELECT id FROM leads WHERE call_id = $1 LIMIT 1", call_id)
                    if l_row:
                        outcome = "warm_lead"

                # 3. Short / dropped call
                if not outcome and len(lines) <= 2 and duration < 25:
                    outcome = "dropped_call"

                # 4. Live transfer
                if not outcome and (outcome == "transferred" or explicit_outcome == "transferred"):
                    outcome = "transferred"

                # 5. AI / rule-based classification
                if not outcome:
                    ai_res = await _classify_transcript_with_ai(transcript)
                    if ai_res:
                        outcome = ai_res
                    else:
                        t_lower = transcript.lower()
                        if any(w in t_lower for w in ["send me", "email", "booking link", "busy right now", "call back", "quote", "proposal", "check with my", "think about it"]):
                            outcome = "warm_lead"
                        elif any(w in t_lower for w in ["transfer", "human", "agent", "specialist"]):
                            outcome = "transferred"
                        else:
                            outcome = "info_inquiry"

                # 6. Auto-log warm lead if not already logged
                if outcome == "warm_lead" and call_id:
                    l_chk = await conn.fetchrow("SELECT id FROM leads WHERE call_id = $1 LIMIT 1", call_id)
                    if not l_chk:
                        service = "General Services"
                        t_lower = transcript.lower()
                        if "website" in t_lower or "web dev" in t_lower:
                            service = "Web Development & SEO" if "seo" in t_lower else "Web Development"
                        elif "seo" in t_lower:
                            service = "SEO"
                        elif "voice agent" in t_lower or "receptionist" in t_lower:
                            service = "AI Voice Agents"
                        elif "automation" in t_lower:
                            service = "AI Automation"
                        elif "content" in t_lower or "video" in t_lower:
                            service = "Content Creation"

                        await conn.execute(
                            """
                            INSERT INTO leads (name, phone, interested_service, reason, status, call_id)
                            VALUES ($1, $2, $3, $4, 'warm', $5)
                            """,
                            "Caller (Interested Lead)",
                            caller_phone or "Unknown",
                            service,
                            "Showed interest / requested follow-up during call",
                            call_id,
                        )

                # Insert into call_logs
                await conn.execute(
                    """
                    INSERT INTO call_logs (call_id, caller_phone, duration_seconds, transcript, outcome)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    call_id,
                    caller_phone,
                    duration,
                    transcript or "No transcript captured",
                    outcome,
                )
                logger.info(f"Call record saved in PostgreSQL: {outcome}, {duration}s, caller={caller_phone}, id={call_id}")
                return True
        except Exception as e:
            logger.warning(f"PostgreSQL call log save failed: {e}")
            return False

    # Fallback to Supabase REST if PostgreSQL pool is not available
    supabase_url, supabase_key = _get_supabase_config()
    if not supabase_url or not supabase_key:
        logger.error("DATABASE_URL or Supabase credentials not set — call not logged")
        return False

    # 1. Only a real meeting booked via Cal.com / meetings table is "meeting_booked"
    if outcome != "meeting_booked" and call_id:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6)) as session:
                async with session.get(
                    f"{supabase_url}/rest/v1/meetings?call_id=eq.{call_id}&select=id",
                    headers=_headers(supabase_key),
                ) as m_resp:
                    if m_resp.status == 200:
                        m_rows = await m_resp.json()
                        if m_rows:
                            outcome = "meeting_booked"
        except Exception as e:
            logger.debug(f"Meetings check skipped: {e}")

    # 2. Check if already logged as warm lead in database
    if not outcome and call_id:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6)) as session:
                async with session.get(
                    f"{supabase_url}/rest/v1/leads?call_id=eq.{call_id}&select=id",
                    headers=_headers(supabase_key),
                ) as l_resp:
                    if l_resp.status == 200:
                        l_rows = await l_resp.json()
                        if l_rows:
                            outcome = "warm_lead"
        except Exception as e:
            logger.debug(f"Leads check skipped: {e}")

    # 3. Check for dropped call
    if not outcome and len(lines) <= 2 and duration < 25:
        outcome = "dropped_call"

    # 4. Check for live transfer
    if not outcome and (outcome == "transferred" or explicit_outcome == "transferred"):
        outcome = "transferred"

    # 5. Intelligent classification
    if not outcome:
        ai_res = await _classify_transcript_with_ai(transcript)
        if ai_res:
            outcome = ai_res
        else:
            t_lower = transcript.lower()
            if any(w in t_lower for w in ["send me", "email", "booking link", "busy right now", "call back", "quote", "proposal", "check with my", "think about it"]):
                outcome = "warm_lead"
            elif any(w in t_lower for w in ["transfer", "human", "agent", "specialist"]):
                outcome = "transferred"
            else:
                outcome = "info_inquiry"

    # 6. Auto-log warm lead
    if outcome == "warm_lead" and call_id:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6)) as session:
                async with session.get(
                    f"{supabase_url}/rest/v1/leads?call_id=eq.{call_id}&select=id",
                    headers=_headers(supabase_key),
                ) as l_chk:
                    existing_leads = (await l_chk.json()) if l_chk.status == 200 else []
            if not existing_leads:
                service = "General Services"
                t_lower = transcript.lower()
                if "website" in t_lower or "web dev" in t_lower:
                    service = "Web Development & SEO" if "seo" in t_lower else "Web Development"
                elif "seo" in t_lower:
                    service = "SEO"
                elif "voice agent" in t_lower or "receptionist" in t_lower:
                    service = "AI Voice Agents"
                elif "automation" in t_lower:
                    service = "AI Automation"
                elif "content" in t_lower or "video" in t_lower:
                    service = "Content Creation"

                await create_lead(
                    name="Caller (Interested Lead)",
                    phone=caller_phone or "Unknown",
                    interested_service=service,
                    reason="Showed interest / requested follow-up during call",
                    call_id=call_id,
                )
        except Exception as e:
            logger.debug(f"Auto lead creation skipped: {e}")

    payload = {
        "call_id": call_id,
        "caller_phone": caller_phone,
        "duration_seconds": duration,
        "transcript": transcript or "No transcript captured",
        "outcome": outcome,
    }

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6)) as session:
            async with session.post(
                f"{supabase_url}/rest/v1/call_logs",
                json=payload,
                headers=_headers(supabase_key, prefer="return=minimal"),
            ) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    logger.warning(f"Call log insert failed ({resp.status}): {body}")
                    return False
                else:
                    logger.info(f"Call record saved: {outcome}, {duration}s, caller={caller_phone}, id={call_id}")
                    return True
    except Exception as e:
        logger.warning(f"Call log request failed: {e}")
        return False
