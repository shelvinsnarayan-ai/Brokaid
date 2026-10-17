"""Phase R5c-1a — Mongo mirror of Brain._read_sprint.

Byte-parity across backends: same return shape
(tokens_used, last_alert_pct). The Mongo projection is populated by
`workflow_mongo.project_event` on every `AgentOutputProduced`
(tokens_used upsert) and `SprintCapAlert` (last_alert_pct update).

Read is tenant-scoped via `TenantSafeDatabase.proj_sprint_usage`
under `use_tenant(tid)`.
"""
from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.tenant_safe import wrap as _wrap_safe
from app.kernel.tenant_ctx import use_tenant


async def read_sprint(store, tenant_id: str) -> tuple[int, int]:
    """Return `(tokens_used, last_alert_pct)` from proj_sprint_usage
    on Mongo. Byte-identical return shape to `Brain._read_sprint` on
    Postgres."""
    safe = (
        _wrap_safe(store) if isinstance(store, AsyncIOMotorDatabase) else store
    )
    async with use_tenant(tenant_id):
        row = await safe.proj_sprint_usage.find_one({})
    if not row:
        return 0, 0
    return (
        int(row.get("tokens_used") or 0),
        int(row.get("last_alert_pct") or 0),
    )
