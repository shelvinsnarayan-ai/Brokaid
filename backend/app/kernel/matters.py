"""Matters kernel — thin dispatch-aware `create_matter`.

R5a-1: introduced as a NEW kernel module (no prior in-line
implementation existed in the routes — MatterCreated was only
handled on the projection side). Callers today: E2E scene 1 test,
and the R5b intake+triage route rewire.

Under Postgres: emits MatterCreated via `trail.append` → the
in-tx `_project` populates `proj_matters` in the same transaction.

Under Mongo: emits MatterCreated via `trail_mongo.append_event` →
its in-line projection dispatch (R5a-1) calls
`workflow_mongo.project_event` which upserts `proj_matters`.

Byte-identical event hash across backends per R2 guarantee.
"""
from __future__ import annotations

import uuid
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.tenant_safe import TenantSafeDatabase
from app.kernel.trail_dispatch import append_via_dispatch


def _is_mongo(store) -> bool:
    return isinstance(store, (AsyncIOMotorDatabase, TenantSafeDatabase))


async def create_matter(
    store,
    *,
    tenant_id: str,
    matter_id: str | None = None,
    client_id: str,
    status: str,
    subject: str,
    actor: dict,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Emit MatterCreated for a new matter. Returns
    `{matter_id, event_id, event_hash}`."""
    matter_id = matter_id or str(uuid.uuid4())
    if _is_mongo(store):
        from app.kernel.tenant_ctx import use_tenant
        async with use_tenant(tenant_id):
            ev = await append_via_dispatch(
                store, tenant_id, "MatterCreated",
                actor=actor,
                payload={
                    "matter_id": matter_id,
                    "client_id": client_id,
                    "status": status,
                    "subject": subject,
                },
                idempotency_key=idempotency_key or f"matter:{matter_id}",
            )
    else:
        ev = await append_via_dispatch(
            store, tenant_id, "MatterCreated",
            actor=actor,
            payload={
                "matter_id": matter_id,
                "client_id": client_id,
                "status": status,
                "subject": subject,
            },
            idempotency_key=idempotency_key or f"matter:{matter_id}",
        )
    return {
        "matter_id": matter_id,
        "event_id": ev["event_id"],
        "event_hash": ev["event_hash"],
    }
