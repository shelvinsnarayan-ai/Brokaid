"""Restart-safe workflow saga.

Every wait and every step is a ledger event.  On restart, `resume()`
reads the last event for the workflow and emits the next step with a
deterministic idempotency_key so duplicate calls are no-ops.
"""
from __future__ import annotations

from typing import Any

import asyncpg

from app.kernel.trail import append


async def emit_step(
    pool: asyncpg.Pool,
    tenant_id: str,
    workflow_id: str,
    step_num: int,
    payload: dict[str, Any] | None = None,
) -> None:
    """Emit workflow step `step_num`. Idempotent."""
    p = dict(payload or {})
    p["workflow_id"] = workflow_id
    p["step"] = step_num
    try:
        await append(
            pool, tenant_id, "WorkflowStep",
            actor={"kind": "system", "id": "workflow", "name": "wf"},
            payload=p,
            idempotency_key=f"wf:{workflow_id}:{step_num}",
        )
    except asyncpg.UniqueViolationError:
        # Already emitted — expected on resume.
        pass


async def last_step(
    pool: asyncpg.Pool, tenant_id: str, workflow_id: str
) -> int:
    """Return the highest step_num already emitted for workflow_id, or 0."""
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            row = await c.fetchrow(
                """
                SELECT COALESCE(MAX((payload->>'step')::int), 0) AS s
                  FROM events
                 WHERE tenant_id = $1::uuid
                   AND type = 'WorkflowStep'
                   AND payload->>'workflow_id' = $2
                """,
                tenant_id, workflow_id,
            )
    return int(row["s"] or 0)


async def resume(
    pool: asyncpg.Pool, tenant_id: str, workflow_id: str, total_steps: int
) -> int:
    """Advance the workflow to completion. Returns steps actually emitted this call."""
    start = await last_step(pool, tenant_id, workflow_id)
    emitted = 0
    for step in range(start + 1, total_steps + 1):
        await emit_step(pool, tenant_id, workflow_id, step)
        emitted += 1
    return emitted
