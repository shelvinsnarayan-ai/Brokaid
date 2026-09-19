"""Bounded timer sweep job (dispatch-aware).

Fires TimerFired for every proj_workflow_waits row whose fire_at is
past now() and which isn't yet resolved. Advisory-locked so
overlapping invocations are no-ops.

R5c-3: dispatches over Pool + Motor. Under Mongo, the fire-at query
targets `safe.proj_workflow_waits` under `use_tenant(tid)`.

Run standalone:
    python -m app.jobs.timer_sweep
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.db.tenant_safe import TenantSafeDatabase, wrap as _wrap_safe
from app.jobs._tenants import list_tenants
from app.kernel.locks import Lock
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch

logger = logging.getLogger("brokaid.timer_sweep")
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
)

MAX_RUNTIME_SEC = 25   # 30s schedule → 5s guard band
ADVISORY_KEY = 0x74696D65727377  # ascii 'timersw'


def _is_mongo(store) -> bool:
    return isinstance(store, (AsyncIOMotorDatabase, TenantSafeDatabase))


async def _waits_for_tenant_pg(pool, tenant_id: str) -> list[dict]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id,
            )
            rows = await conn.fetch(
                """
                SELECT workflow_id, step
                  FROM proj_workflow_waits
                 WHERE tenant_id = $1::uuid
                   AND fire_at IS NOT NULL
                   AND fire_at <= NOW()
                   AND NOT resolved
                """,
                tenant_id,
            )
    return [{"workflow_id": r["workflow_id"], "step": r["step"]} for r in rows]


async def _waits_for_tenant_mongo(store, tenant_id: str) -> list[dict]:
    safe = _wrap_safe(store)
    now = datetime.now(timezone.utc)
    out: list[dict] = []
    async with use_tenant(tenant_id):
        cursor = safe.proj_workflow_waits.find({
            "resolved": False,
            "fire_at": {"$ne": None, "$lte": now},
        })
        async for r in cursor:
            out.append({"workflow_id": r["workflow_id"], "step": r["step"]})
    return out


async def sweep_once(store) -> int:
    """Fire any ready timers. Returns count fired."""
    lock = Lock(
        store, key=ADVISORY_KEY,
        holder_id=f"timer_sweep:{datetime.now(timezone.utc).isoformat()}",
        ttl_sec=MAX_RUNTIME_SEC + 5,
    )
    if not await lock.try_acquire():
        logger.info("another sweep is in flight — exiting quietly")
        return 0
    try:
        fired = 0
        start = datetime.now(timezone.utc)
        tenants = await list_tenants(store)
        for t in tenants:
            if (
                (datetime.now(timezone.utc) - start).total_seconds()
                > MAX_RUNTIME_SEC
            ):
                logger.warning("max runtime exceeded — stopping mid-sweep")
                break
            tenant_id = t["tenant_id"]
            if _is_mongo(store):
                waits = await _waits_for_tenant_mongo(store, tenant_id)
            else:
                waits = await _waits_for_tenant_pg(store, tenant_id)
            for w in waits:
                try:
                    await append_via_dispatch(
                        store, tenant_id, "TimerFired",
                        actor={"kind": "system", "id": "timer_sweep",
                               "name": "sweep"},
                        payload={
                            "workflow_id": w["workflow_id"],
                            "step": w["step"],
                        },
                        idempotency_key=(
                            f"timer:{w['workflow_id']}:{w['step']}"
                        ),
                    )
                    fired += 1
                except (Exception, DuplicateKeyError):
                    pass  # already fired — benign
        return fired
    finally:
        await lock.release()


async def _main() -> int:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    from app.jobs.chain_verify import _close_store, _open_store
    store = await _open_store()
    try:
        fired = await sweep_once(store)
        logger.info("timer_sweep: %d timers fired", fired)
        return 0
    except Exception as e:
        logger.error("timer_sweep failed: %r", e)
        return 1
    finally:
        await _close_store(store)


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
