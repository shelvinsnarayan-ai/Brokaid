"""Autonomy unlock protocol.

Update rule:
  unlocked = decisions_seen >= 50
            AND agreements/decisions_seen >= 0.95
            AND eno_misses = 0

On any decision where agreed=false AND eno_flag=true → relock.

Projection updates are applied in-transaction inside trail._project()
so they are always ledger-consistent.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import asyncpg


UNLOCK_THRESHOLD_DECISIONS = 50
UNLOCK_THRESHOLD_AGREEMENT = 0.95


async def apply_human_decision(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    class_key: str,
    agreed_with_agent: bool,
    eno_flag: bool,
    event_id: str,
) -> tuple[bool, str | None]:
    """Update the class-unlock projection for one human decision.

    Returns (state_changed_event_needed, new_state_kind) where
    new_state_kind is 'ClassUnlocked' or 'ClassRelocked' or None.
    Caller emits the corresponding event.
    """
    # upsert current row + read back
    row = await conn.fetchrow(
        """
        INSERT INTO proj_class_unlock
          (tenant_id, class_key, decisions_seen, agreements, eno_misses,
           unlocked, updated_by_event)
        VALUES ($1::uuid, $2, 0, 0, 0, FALSE, $3::uuid)
        ON CONFLICT (tenant_id, class_key) DO UPDATE
          SET updated_by_event = EXCLUDED.updated_by_event
        RETURNING decisions_seen, agreements, eno_misses, unlocked
        """,
        tenant_id, class_key, event_id,
    )
    decisions_seen = row["decisions_seen"] + 1
    agreements = row["agreements"] + (1 if agreed_with_agent else 0)
    eno_misses = row["eno_misses"] + (1 if (not agreed_with_agent and eno_flag) else 0)

    prev_unlocked = row["unlocked"]
    relock = (not agreed_with_agent) and eno_flag
    if relock:
        unlocked = False
    else:
        rate = agreements / decisions_seen if decisions_seen else 0.0
        unlocked = (
            decisions_seen >= UNLOCK_THRESHOLD_DECISIONS
            and rate >= UNLOCK_THRESHOLD_AGREEMENT
            and eno_misses == 0
        )

    now = datetime.now(timezone.utc)
    fields: dict[str, Any] = {
        "decisions_seen": decisions_seen,
        "agreements": agreements,
        "eno_misses": eno_misses,
        "unlocked": unlocked,
    }
    if unlocked and not prev_unlocked:
        fields["unlocked_at"] = now
    if relock and prev_unlocked:
        fields["last_relock_at"] = now
        fields["last_relock_reason"] = "eno_flag miss"

    await conn.execute(
        """
        UPDATE proj_class_unlock
           SET decisions_seen = $3, agreements = $4, eno_misses = $5,
               unlocked = $6,
               unlocked_at = COALESCE($7, unlocked_at),
               last_relock_at = COALESCE($8, last_relock_at),
               last_relock_reason = COALESCE($9, last_relock_reason),
               updated_by_event = $10::uuid
         WHERE tenant_id = $1::uuid AND class_key = $2
        """,
        tenant_id, class_key, decisions_seen, agreements, eno_misses,
        unlocked,
        fields.get("unlocked_at"), fields.get("last_relock_at"),
        fields.get("last_relock_reason"),
        event_id,
    )

    if unlocked and not prev_unlocked:
        return True, "ClassUnlocked"
    if relock and prev_unlocked:
        return True, "ClassRelocked"
    return False, None


async def is_unlocked(
    conn: asyncpg.Connection, tenant_id: str, class_key: str
) -> bool:
    row = await conn.fetchrow(
        "SELECT unlocked FROM proj_class_unlock "
        "WHERE tenant_id = $1::uuid AND class_key = $2",
        tenant_id, class_key,
    )
    return bool(row and row["unlocked"])
