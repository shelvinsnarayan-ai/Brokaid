"""Compliance block/clear — dispatch-aware across Postgres + Mongo.

R5a-1: rewrote as dispatcher over `store`. Postgres path preserved
byte-for-byte (calls `trail.append` + reads `proj_active_blocks`
via SQL). Mongo path calls `trail_dispatch.append_via_dispatch`,
which auto-projects `ComplianceBlockAdded/Cleared` into
`proj_active_blocks` via `trail_mongo.append_event`'s in-line
projection dispatch.
"""
from __future__ import annotations

import uuid
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.tenant_safe import TenantSafeDatabase
from app.kernel.trail import append as _pg_append
from app.kernel.trail_dispatch import append_via_dispatch


def _is_mongo(store) -> bool:
    return isinstance(store, (AsyncIOMotorDatabase, TenantSafeDatabase))


def _is_pg(store) -> bool:
    return isinstance(store, object)


async def add_block(
    store,
    *,
    tenant_id: str,
    target_kind: str,
    target_id: str,
    reason: str,
    actor: dict,
) -> str:
    """Emit ComplianceBlockAdded + return block_id. Backend-polymorphic."""
    block_id = str(uuid.uuid4())
    if _is_mongo(store):
        from app.kernel.tenant_ctx import use_tenant
        async with use_tenant(tenant_id):
            await append_via_dispatch(
                store, tenant_id, "ComplianceBlockAdded",
                actor=actor,
                payload={
                    "block_id": block_id,
                    "target_kind": target_kind,
                    "target_id": target_id,
                    "reason": reason,
                },
                idempotency_key=f"compl-add:{block_id}",
            )
    else:
        await _pg_append(
            store, tenant_id, "ComplianceBlockAdded",
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
    store,
    *,
    tenant_id: str,
    block_id: str,
    reason: str,
    actor: dict,
) -> None:
    if _is_mongo(store):
        from app.kernel.tenant_ctx import use_tenant
        async with use_tenant(tenant_id):
            await append_via_dispatch(
                store, tenant_id, "ComplianceBlockCleared",
                actor=actor,
                payload={"block_id": block_id, "reason": reason},
                idempotency_key=f"compl-clear:{block_id}",
            )
    else:
        await _pg_append(
            store, tenant_id, "ComplianceBlockCleared",
            actor=actor,
            payload={"block_id": block_id, "reason": reason},
            idempotency_key=f"compl-clear:{block_id}",
        )


async def active_block_id(
    store,
    tenant_id: str,
    target_kind: str,
    target_id: str,
) -> Optional[str]:
    if _is_mongo(store):
        from app.db.tenant_safe import wrap
        from app.kernel.tenant_ctx import use_tenant
        safe = wrap(store)
        async with use_tenant(tenant_id):
            row = await safe.proj_active_blocks.find_one(
                {"target_kind": target_kind,
                 "target_id": target_id,
                 "cleared_at": None},
                sort=[("created_at", -1)],
            )
        return row["block_id"] if row else None
    async with store.acquire() as c:
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


# ---- Projection helper for Mongo cleared_at flag ---------------------
# `trail_mongo.append_event`'s auto-projection sets `cleared_at` via
# `workflow_mongo.clear_active_block` on ComplianceBlockCleared. The
# Mongo `proj_active_blocks` shape mirrors Postgres. Nothing extra
# to do here — the `find_one` above uses the same shape.
