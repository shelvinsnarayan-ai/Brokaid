"""Phase R4b — workflow-layer dispatcher.

Thin router over Postgres and Mongo backends for the workflow
collections (envelopes, proj_queue, proj_active_blocks). Follows
the R4a `identity.py` pattern: store type picks the backend, wrong
type raises.

Postgres side re-uses `app.kernel.queue.*` for queue ops and
inline SQL for envelopes/blocks (unchanged from R3). Mongo side
delegates to `workflow_mongo`.

R4b keeps default backend = Postgres. Routes are NOT rewired here
— they still call `queue.enqueue(pool, ...)` etc. R5 will migrate
callers.
"""
from __future__ import annotations

import os

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.tenant_safe import TenantSafeDatabase
from app.kernel import queue as _pg_queue
from app.kernel import workflow_mongo


def active_backend() -> str:
    """Read `BROKAID_LEDGER` at call time (workflow projections live
    on the same store as the ledger). Default `postgres`."""
    return os.environ.get("BROKAID_LEDGER", "mongo").lower()


class WorkflowBackendMismatch(RuntimeError):
    pass


def _is_mongo(store) -> bool:
    return isinstance(store, (AsyncIOMotorDatabase, TenantSafeDatabase))


def _is_pg(store) -> bool:
    # R5c-4b — Postgres runtime deleted; nothing is a PG store.
    return False


# ---- Queue helpers (public API mirrored across backends) --------------
async def enqueue(store, *, tenant_id: str, envelope_id: str,
                  class_key: str, proposal_ref: str, **kwargs):
    if _is_mongo(store):
        from app.kernel.tenant_ctx import use_tenant
        async with use_tenant(tenant_id):
            return await workflow_mongo.enqueue(
                store, envelope_id=envelope_id, class_key=class_key,
                proposal_ref=proposal_ref, **kwargs,
            )
    if _is_pg(store):
        return await _pg_queue.enqueue(
            store, tenant_id=tenant_id, envelope_id=envelope_id,
            class_key=class_key, proposal_ref=proposal_ref, **kwargs,
        )
    raise WorkflowBackendMismatch(f"unknown store type: {type(store).__name__}")


async def fetch_pending_queue(store, tenant_id: str):
    if _is_mongo(store):
        from app.kernel.tenant_ctx import use_tenant
        async with use_tenant(tenant_id):
            return await workflow_mongo.fetch_pending_queue(store)
    if _is_pg(store):
        return await _pg_queue.fetch_pending(store, tenant_id)
    raise WorkflowBackendMismatch(f"unknown store type: {type(store).__name__}")


async def fetch_queue_card(store, tenant_id: str, envelope_id: str):
    if _is_mongo(store):
        from app.kernel.tenant_ctx import use_tenant
        async with use_tenant(tenant_id):
            return await workflow_mongo.fetch_queue_card(store, envelope_id)
    if _is_pg(store):
        return await _pg_queue.fetch_card(store, tenant_id, envelope_id)
    raise WorkflowBackendMismatch(f"unknown store type: {type(store).__name__}")


async def resolve_queue_card(store, tenant_id: str, envelope_id: str,
                              resolved_by_event: str):
    if _is_mongo(store):
        from app.kernel.tenant_ctx import use_tenant
        async with use_tenant(tenant_id):
            return await workflow_mongo.resolve_queue_card(
                store, envelope_id, resolved_by_event,
            )
    if _is_pg(store):
        return await _pg_queue.resolve(
            store, tenant_id=tenant_id, envelope_id=envelope_id,
            resolved_by_event=resolved_by_event,
        )
    raise WorkflowBackendMismatch(f"unknown store type: {type(store).__name__}")


__all__ = [
    "WorkflowBackendMismatch",
    "active_backend",
    "enqueue", "fetch_pending_queue", "fetch_queue_card",
    "resolve_queue_card",
]
