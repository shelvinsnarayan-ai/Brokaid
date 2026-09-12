"""Compliance block/clear — ledger events + proj_active_blocks projection.

Public API:
- `add_block(pool, tenant_id, target_kind, target_id, reason, actor) -> block_id`
- `clear_block(pool, tenant_id, block_id, reason, actor) -> None`
- `active_block_id(pool, tenant_id, target_kind, target_id) -> str | None`
- `evaluate_with_compliance(...)` — thin wrapper around gAIte.evaluate
  that fetches the active block for the envelope target and forwards it.
"""
from __future__ import annotations

import uuid
from typing import Optional

import asyncpg

from app.kernel.trail import append


async def add_block(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    target_kind: str,
    target_id: str,
    reason: str,
    actor: dict,
) -> str:
    block_id = str(uuid.uuid4())
    await append(
        pool, tenant_id, "ComplianceBlockAdded",
        actor=actor,
        payload={
            "block_id": block_id,
            "target_kind": target_kind,
            "target_id": target_id,
            "reason": reason,
        },
        idempotency_key=f"compl-add:{block_id}",
    )
    return block_id


async def clear_block(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    block_id: str,
    reason: str,
    actor: dict,
) -> None:
    await append(
        pool, tenant_id, "ComplianceBlockCleared",
        actor=actor,
        payload={"block_id": block_id, "reason": reason},
        idempotency_key=f"compl-clear:{block_id}",
    )


async def active_block_id(
    pool: asyncpg.Pool,
    tenant_id: str,
    target_kind: str,
    target_id: str,
) -> Optional[str]:
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            row = await c.fetchrow(
                """
                SELECT block_id::text FROM proj_active_blocks
                 WHERE tenant_id = $1::uuid
                   AND target_kind = $2
                   AND target_id = $3
                   AND cleared_at IS NULL
                 ORDER BY created_at DESC LIMIT 1
                """,
                tenant_id, target_kind, target_id,
            )
    return row["block_id"] if row else None
