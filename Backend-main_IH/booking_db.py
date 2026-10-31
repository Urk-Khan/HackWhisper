"""
Compatibility database wrapper for HackWhisper.
Delegates to event_manager for PostgreSQL pooling, event management, and call logging.
"""

from typing import Optional, List, Any
import event_manager


async def get_db_pool():
    return await event_manager.get_db_pool()


async def init_db():
    await event_manager.init_db()


async def get_customer_by_phone(phone: Optional[str]) -> Optional[dict]:
    """Looks up whether a caller has previous notifications or interactions."""
    if not phone:
        return None
    pool = await event_manager.get_db_pool()
    if not pool:
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT recipient, channel, created_at
                FROM event_notifications
                WHERE recipient = $1
                ORDER BY created_at DESC
                LIMIT 1
                """,
                phone,
            )
            if row:
                return {
                    "phone": row["recipient"],
                    "last_channel": row["channel"],
                    "interacted": True,
                }
    except Exception:
        pass
    return None


async def save_call_record(
    call_id: str,
    caller_phone: Optional[str],
    start_time: float,
    messages: List[Any],
    explicit_outcome: Optional[str] = None,
):
    await event_manager.save_call_record(
        call_id=call_id,
        caller_phone=caller_phone,
        start_time=start_time,
        messages=messages,
        explicit_outcome=explicit_outcome,
    )
