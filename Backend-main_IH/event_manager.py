"""
HackWhisper — Hacktoberfest Event Management & PostgreSQL Database Layer.

Handles:
1. Daily full fetch from Hacktoberfest MLH Events API (https://hacktoberfest-api.mlh.com/api/events).
2. Upsert with Historical Versioning:
   - Inserts new events.
   - Detects updates and archives previous versions into `event_history` table before updating.
   - Preserves all historical events without deletion.
   - Logs sync execution into `event_sync_logs`.
3. Rapid in-memory cached lookups + asyncpg PostgreSQL queries for:
   - search_events(city)
   - get_event_details(event_id)
   - get_registration_link(event_id)
4. Audit logging for sent emails/SMS notifications and call transcripts.
"""

import asyncio
from datetime import datetime, timezone
import json
import os
import re
from typing import Any, Dict, List, Optional
import aiohttp
from loguru import logger

try:
    import asyncpg
except ImportError:
    asyncpg = None

HACKTOBERFEST_API_URL = os.getenv(
    "HACKTOBERFEST_EVENTS_API", "https://hacktoberfest-api.mlh.com/api/events"
)

_pool: "Optional[asyncpg.Pool]" = None
_pool_lock = asyncio.Lock()

# Fast in-memory cache of events for sub-millisecond voice tool responses
_MEM_CACHE: Dict[str, dict] = {}
_LAST_SYNC_TIME: Optional[datetime] = None


def _get_db_url() -> str:
    """Returns database connection URL from environment variables."""
    url = (
        os.getenv("DATABASE_URL")
        or os.getenv("POSTGRES_URL")
        or os.getenv("POSTGRESQL_URL")
        or ""
    ).strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    return url


async def get_db_pool() -> "Optional[asyncpg.Pool]":
    """Maintains an asyncpg connection pool."""
    global _pool
    if _pool and not _pool._closed:
        return _pool

    if asyncpg is None:
        logger.warning("asyncpg is not installed. Database persistence disabled; using in-memory cache.")
        return None

    db_url = _get_db_url()
    if not db_url:
        logger.info("DATABASE_URL not set. Running with high-speed in-memory event cache.")
        return None

    async with _pool_lock:
        if _pool and not _pool._closed:
            return _pool
        try:
            _pool = await asyncpg.create_pool(
                db_url,
                min_size=1,
                max_size=10,
                command_timeout=15.0,
                statement_cache_size=0,
            )
            logger.info("Connected to PostgreSQL database for Hacktoberfest events.")
            return _pool
        except Exception as e:
            logger.error(f"Failed to connect to PostgreSQL ({e}). Using in-memory fallback cache.")
            return None


async def init_db() -> None:
    """Initializes tables and schema if PostgreSQL is connected."""
    pool = await get_db_pool()
    if not pool:
        return

    schema_path = os.path.join(os.path.dirname(__file__), "hacktoberfest_schema.sql")
    if not os.path.exists(schema_path):
        return

    try:
        with open(schema_path, "r", encoding="utf-8") as f:
            sql = f.read()

        async with pool.acquire() as conn:
            await conn.execute(sql)
            logger.info("Hacktoberfest database schema verified successfully.")
    except Exception as e:
        logger.error(f"Error initializing database schema: {e}")


def _normalize_event_payload(item: dict) -> dict:
    """Extracts and cleans fields from raw MLH Hacktoberfest API item."""
    addr = item.get("address") or {}
    return {
        "id": str(item.get("id") or item.get("slug") or ""),
        "name": (item.get("name") or "").strip(),
        "slug": (item.get("slug") or "").strip(),
        "format": (item.get("format") or "").strip(),
        "status": (item.get("status") or "").strip(),
        "starts_at": item.get("startsAt"),
        "ends_at": item.get("endsAt"),
        "timezone": item.get("timeZone") or "UTC",
        "description": (item.get("description") or "").strip(),
        "city": (addr.get("city") or "").strip(),
        "state": (addr.get("state") or "").strip(),
        "country": (addr.get("country") or "").strip(),
        "address_line1": (addr.get("line1") or "").strip(),
        "address_line2": (addr.get("line2") or "").strip(),
        "postal_code": (addr.get("postalCode") or "").strip(),
        "latitude": addr.get("latitude"),
        "longitude": addr.get("longitude"),
        "website_url": (item.get("websiteUrl") or "").strip(),
        "registration_url": (item.get("registrationUrl") or "").strip(),
        "logo_url": (item.get("logoUrl") or "").strip(),
        "raw_data": json.dumps(item),
    }


async def fetch_events_from_api() -> List[dict]:
    """Fetches full event list from Hacktoberfest MLH Events API."""
    url = os.getenv("HACKTOBERFEST_EVENTS_API", "https://hacktoberfest-api.mlh.com/api/events")
    headers = {"User-Agent": "HackWhisper-Voice-Agent/1.0"}
    logger.info(f"Fetching events from Hacktoberfest API: {url}...")
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"Hacktoberfest API returned HTTP {resp.status}: {text[:200]}")
                    return []
                data = await resp.json()
                if isinstance(data, dict):
                    events = data.get("events", [])
                elif isinstance(data, list):
                    events = data
                else:
                    events = []
                logger.info(f"Successfully fetched {len(events)} events from API.")
                return events
    except Exception as e:
        logger.error(f"Failed to fetch Hacktoberfest events from API: {e}")
        return []


async def sync_events() -> dict:
    """
    Automatic Event Management:
    Daily Full Fetch + Upsert + Historical Versioning.
    - Adds new events with version = 1.
    - Compares existing events: if modified, archives old version into `event_history` table
      and updates the event row with version = version + 1 and updated_at = NOW().
    - Never deletes past/ended events.
    - Saves execution summary into `event_sync_logs`.
    """
    global _LAST_SYNC_TIME, _MEM_CACHE

    raw_events = await fetch_events_from_api()
    if not raw_events:
        logger.warning("No events retrieved during sync cycle.")
        return {"success": False, "total": 0, "new": 0, "updated": 0, "error": "api_fetch_failed"}

    total_fetched = len(raw_events)
    new_count = 0
    updated_count = 0
    pool = await get_db_pool()

    now = datetime.now(timezone.utc)

    # 1. Update in-memory cache first for immediate voice response availability
    for item in raw_events:
        norm = _normalize_event_payload(item)
        if norm["id"]:
            _MEM_CACHE[norm["id"]] = norm

    _LAST_SYNC_TIME = now

    # 2. Upsert into PostgreSQL if pool is available
    if pool:
        try:
            async with pool.acquire() as conn:
                for item in raw_events:
                    norm = _normalize_event_payload(item)
                    event_id = norm["id"]
                    if not event_id:
                        continue

                    # Check if already exists in DB
                    existing = await conn.fetchrow(
                        "SELECT id, name, starts_at, ends_at, status, description, registration_url, city, version, raw_data "
                        "FROM events WHERE id = $1",
                        event_id,
                    )

                    if existing is None:
                        # New event insert with version = 1
                        await conn.execute(
                            """
                            INSERT INTO events (
                                id, name, slug, format, status, starts_at, ends_at, timezone,
                                description, city, state, country, address_line1, address_line2,
                                postal_code, latitude, longitude, website_url, registration_url,
                                logo_url, raw_data, version, created_at, updated_at
                            ) VALUES (
                                $1, $2, $3, $4, $5, $6::timestamptz, $7::timestamptz, $8,
                                $9, $10, $11, $12, $13, $14,
                                $15, $16, $17, $18, $19,
                                $20, $21::jsonb, 1, NOW(), NOW()
                            )
                            """,
                            norm["id"], norm["name"], norm["slug"], norm["format"], norm["status"],
                            norm["starts_at"], norm["ends_at"], norm["timezone"], norm["description"],
                            norm["city"], norm["state"], norm["country"], norm["address_line1"],
                            norm["address_line2"], norm["postal_code"], norm["latitude"],
                            norm["longitude"], norm["website_url"], norm["registration_url"],
                            norm["logo_url"], norm["raw_data"],
                        )
                        new_count += 1
                    else:
                        # Check for meaningful changes to trigger historical versioning
                        has_changed = (
                            existing["name"] != norm["name"]
                            or existing["status"] != norm["status"]
                            or existing["description"] != norm["description"]
                            or existing["registration_url"] != norm["registration_url"]
                            or existing["city"] != norm["city"]
                        )

                        if has_changed:
                            curr_version = existing["version"] or 1
                            # 1. Archive previous state into event_history
                            old_snapshot = {
                                "name": existing["name"],
                                "status": existing["status"],
                                "starts_at": str(existing["starts_at"]) if existing["starts_at"] else None,
                                "ends_at": str(existing["ends_at"]) if existing["ends_at"] else None,
                                "description": existing["description"],
                                "registration_url": existing["registration_url"],
                                "city": existing["city"],
                                "raw_data": existing["raw_data"],
                            }
                            await conn.execute(
                                """
                                INSERT INTO event_history (event_id, version, snapshot, changed_at)
                                VALUES ($1, $2, $3::jsonb, NOW())
                                """,
                                event_id,
                                curr_version,
                                json.dumps(old_snapshot),
                            )

                            # 2. Update events record with version + 1
                            new_version = curr_version + 1
                            await conn.execute(
                                """
                                UPDATE events SET
                                    name = $2, slug = $3, format = $4, status = $5,
                                    starts_at = $6::timestamptz, ends_at = $7::timestamptz,
                                    timezone = $8, description = $9, city = $10, state = $11,
                                    country = $12, address_line1 = $13, address_line2 = $14,
                                    postal_code = $15, latitude = $16, longitude = $17,
                                    website_url = $18, registration_url = $19, logo_url = $20,
                                    raw_data = $21::jsonb, version = $22, updated_at = NOW()
                                WHERE id = $1
                                """,
                                norm["id"], norm["name"], norm["slug"], norm["format"], norm["status"],
                                norm["starts_at"], norm["ends_at"], norm["timezone"], norm["description"],
                                norm["city"], norm["state"], norm["country"], norm["address_line1"],
                                norm["address_line2"], norm["postal_code"], norm["latitude"],
                                norm["longitude"], norm["website_url"], norm["registration_url"],
                                norm["logo_url"], norm["raw_data"], new_version,
                            )
                            updated_count += 1

                # 3. Save sync execution log
                await conn.execute(
                    """
                    INSERT INTO event_sync_logs (total_fetched, new_events_count, updated_events_count, status)
                    VALUES ($1, $2, $3, 'success')
                    """,
                    total_fetched,
                    new_count,
                    updated_count,
                )
        except Exception as e:
            logger.error(f"Error during database sync upsert: {e}")
            try:
                async with pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO event_sync_logs (total_fetched, new_events_count, updated_events_count, status, error_message)
                        VALUES ($1, $2, $3, 'failed', $4)
                        """,
                        total_fetched, new_count, updated_count, str(e),
                    )
            except Exception:
                pass

    logger.info(
        f"Event sync complete: {total_fetched} total fetched | {new_count} new | {updated_count} updated."
    )
    return {
        "success": True,
        "total": total_fetched,
        "new": new_count,
        "updated": updated_count,
        "synced_at": now.isoformat(),
    }


async def start_daily_sync_scheduler() -> None:
    """
    Background worker that runs the event sync at startup and repeats every 24 hours.
    """
    logger.info("Initializing daily event sync scheduler (24-hour cycle)...")
    await init_db()
    # Run immediate sync on boot
    await sync_events()

    async def _loop():
        while True:
            try:
                # 24 hours = 86400 seconds
                await asyncio.sleep(86400)
                logger.info("24 hours elapsed. Running scheduled daily Hacktoberfest event sync...")
                await sync_events()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in daily sync scheduler: {e}")
                await asyncio.sleep(300)  # Retry in 5 minutes on transient failure

    asyncio.create_task(_loop())


# =====================================================================
# Voice Agent Query Helpers (search_events, get_event_details, etc.)
# =====================================================================


def _format_event_time(iso_str: Optional[str]) -> str:
    """Converts ISO timestamp into readable spoken format (e.g., 'October 15 at 10:00 AM')."""
    if not iso_str:
        return "TBA"
    try:
        clean = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        return dt.strftime("%B %d, %Y at %I:%M %p").replace(" 0", " ")
    except Exception:
        return iso_str


async def search_events_by_city(city: str, limit: int = 5) -> List[dict]:
    """Searches events matching city (case-insensitive) from DB or fast memory cache."""
    clean_city = (city or "").strip().lower()
    results = []

    # Try PostgreSQL first if pool is alive
    pool = await get_db_pool()
    if pool:
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, name, starts_at, ends_at, city, state, country, status,
                           format, registration_url, website_url, description
                    FROM events
                    WHERE LOWER(city) LIKE $1 OR LOWER(state) LIKE $1 OR LOWER(name) LIKE $1
                    ORDER BY (status = 'active') DESC, starts_at DESC
                    LIMIT $2
                    """,
                    f"%{clean_city}%",
                    limit,
                )
                for r in rows:
                    results.append({
                        "id": r["id"],
                        "name": r["name"],
                        "city": r["city"],
                        "state": r["state"],
                        "country": r["country"],
                        "status": r["status"],
                        "format": r["format"],
                        "starts_at_str": _format_event_time(str(r["starts_at"]) if r["starts_at"] else None),
                        "registration_url": r["registration_url"],
                        "website_url": r["website_url"],
                        "description": r["description"][:200] if r["description"] else "",
                    })
                if results:
                    return results
        except Exception as e:
            logger.warning(f"DB search_events_by_city query failed: {e}. Falling back to memory cache.")

    # In-memory search fallback
    matched = []
    for ev in _MEM_CACHE.values():
        c = (ev.get("city") or "").lower()
        s = (ev.get("state") or "").lower()
        n = (ev.get("name") or "").lower()
        if clean_city in c or clean_city in s or clean_city in n:
            matched.append(ev)

    for ev in matched[:limit]:
        results.append({
            "id": ev["id"],
            "name": ev["name"],
            "city": ev["city"],
            "state": ev["state"],
            "country": ev["country"],
            "status": ev["status"],
            "format": ev["format"],
            "starts_at_str": _format_event_time(ev.get("starts_at")),
            "registration_url": ev["registration_url"],
            "website_url": ev["website_url"],
            "description": ev.get("description", "")[:200],
        })

    return results


async def get_event_by_id_or_name(identifier: str) -> Optional[dict]:
    """Finds an event by UUID id, slug, or matching name."""
    clean_id = (identifier or "").strip().lower()

    # Try in-memory exact match
    if clean_id in _MEM_CACHE:
        ev = _MEM_CACHE[clean_id]
        return {
            **ev,
            "starts_at_str": _format_event_time(ev.get("starts_at")),
            "ends_at_str": _format_event_time(ev.get("ends_at")),
        }

    # DB lookup
    pool = await get_db_pool()
    if pool:
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT id, name, slug, format, status, starts_at, ends_at, timezone,
                           description, city, state, country, address_line1, address_line2,
                           postal_code, website_url, registration_url
                    FROM events
                    WHERE id = $1 OR LOWER(slug) = $2 OR LOWER(name) LIKE $3
                    LIMIT 1
                    """,
                    identifier,
                    clean_id,
                    f"%{clean_id}%",
                )
                if row:
                    return {
                        "id": row["id"],
                        "name": row["name"],
                        "slug": row["slug"],
                        "format": row["format"],
                        "status": row["status"],
                        "timezone": row["timezone"],
                        "city": row["city"],
                        "state": row["state"],
                        "country": row["country"],
                        "address": f"{row['address_line1']} {row['address_line2']}".strip(),
                        "starts_at_str": _format_event_time(str(row["starts_at"]) if row["starts_at"] else None),
                        "ends_at_str": _format_event_time(str(row["ends_at"]) if row["ends_at"] else None),
                        "description": row["description"] or "",
                        "website_url": row["website_url"],
                        "registration_url": row["registration_url"],
                    }
        except Exception as e:
            logger.warning(f"DB get_event_by_id query failed: {e}")

    # Memory substring match
    for ev in _MEM_CACHE.values():
        if clean_id in ev["id"].lower() or clean_id in ev["slug"].lower() or clean_id in ev["name"].lower():
            return {
                **ev,
                "starts_at_str": _format_event_time(ev.get("starts_at")),
                "ends_at_str": _format_event_time(ev.get("ends_at")),
            }

    return None


async def log_notification(
    event_id: str,
    recipient: str,
    channel: str,
    message: str,
    call_id: Optional[str] = None,
    status: str = "sent",
) -> None:
    """Logs sent email or SMS record into event_notifications table."""
    pool = await get_db_pool()
    if not pool:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO event_notifications (event_id, recipient, channel, message_content, status, call_id)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                event_id,
                recipient,
                channel,
                message[:500],
                status,
                call_id,
            )
    except Exception as e:
        logger.warning(f"Failed to log notification to DB: {e}")


async def save_call_record(
    call_id: str,
    caller_phone: Optional[str],
    start_time: float,
    messages: List[Any],
    explicit_outcome: Optional[str] = None,
) -> None:
    """Persists call logs and transcripts to PostgreSQL."""
    pool = await get_db_pool()
    if not pool:
        return

    duration = int(datetime.now().timestamp() - start_time)

    # Compile transcript
    lines = []
    for msg in messages:
        role = getattr(msg, "role", None) or (msg.get("role") if isinstance(msg, dict) else "")
        content = getattr(msg, "content", None) or (msg.get("content") if isinstance(msg, dict) else "")
        if role and content and isinstance(content, str):
            lines.append(f"{role.upper()}: {content}")
    transcript = "\n".join(lines)

    outcome = explicit_outcome or "info_inquiry"

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO call_logs (call_id, caller_phone, duration_seconds, transcript, outcome)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (call_id) DO UPDATE SET
                    duration_seconds = EXCLUDED.duration_seconds,
                    transcript = EXCLUDED.transcript,
                    outcome = EXCLUDED.outcome
                """,
                call_id,
                caller_phone or "unknown",
                duration,
                transcript,
                outcome,
            )
    except Exception as e:
        logger.warning(f"Failed to save call log: {e}")

