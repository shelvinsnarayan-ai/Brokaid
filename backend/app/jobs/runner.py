"""Bounded-job runner (dispatch-aware).

Every scheduled job runs through this module:
  1. Acquire a dispatch-aware advisory lock keyed to the job.
     If not acquired, exit 0 quietly (another instance is running).
  2. Emit `JobRunStarted` under the ops tenant (via
     `trail_dispatch.append_via_dispatch`).
  3. Run the callable under `asyncio.wait_for(max_runtime_sec)`.
  4. Emit `JobRunCompleted` with ok=true/false + optional error.
  5. Release the lock.

R5c-3: `run_job(store, name)` accepts either a Pool or a Motor DB.
The lock primitive (`kernel.locks.Lock`) hides the backend split;
under PG it's `pg_try_advisory_lock` on a dedicated connection,
under Mongo it's an idempotent upsert against `system("_locks")`
with a TTL reclaim.

External schedulers (cron, k8s CronJob, systemd timer) call this
via `python -m app.jobs.run <job_name>`. There is no in-process
scheduler and no always-on worker.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.tenant_safe import TenantSafeDatabase
from app.jobs._tenants import find_by_slug
from app.kernel.locks import Lock
from app.kernel.trail_dispatch import append_via_dispatch

logger = logging.getLogger("brokaid.job_runner")


OPS_TENANT_SLUG = "brokaid-ops"
OPS_TENANT_NAME = "brokAId Ops"
RUNNER_ACTOR = {"kind": "system", "id": "job-runner", "name": "runner"}


JobCallable = Callable[[Any], Awaitable[Any]]


def _is_mongo(store) -> bool:
    return isinstance(store, (AsyncIOMotorDatabase, TenantSafeDatabase))


@dataclass(frozen=True)
class JobDef:
    name: str
    callable: JobCallable
    schedule: str                 # informational only (e.g. "* * * * *")
    advisory_lock_key: int
    max_runtime_sec: float
    description: str = ""


JOBS: dict[str, JobDef] = {}


def register(
    name: str,
    callable: JobCallable,
    *,
    schedule: str,
    advisory_lock_key: int,
    max_runtime_sec: float,
    description: str = "",
    overwrite: bool = False,
) -> JobDef:
    if name in JOBS and not overwrite:
        return JOBS[name]
    JOBS[name] = JobDef(
        name=name, callable=callable, schedule=schedule,
        advisory_lock_key=advisory_lock_key,
        max_runtime_sec=max_runtime_sec, description=description,
    )
    return JOBS[name]


async def _ensure_ops_tenant(store) -> str:
    """R5c-3: dispatch-aware ops-tenant bootstrap."""
    existing = await find_by_slug(store, OPS_TENANT_SLUG)
    if existing is not None:
        return existing["tenant_id"]
    try:
        if _is_mongo(store):
            from app.kernel import trail_mongo
            from app.kernel.tenant_ctx import use_tenant
            new_tid = str(uuid.uuid4())
            async with use_tenant(new_tid):
                t, _ = await trail_mongo.create_tenant(
                    store, OPS_TENANT_SLUG, OPS_TENANT_NAME,
                    tenant_id_override=new_tid,
                )
        else:
            from app.kernel import trail
            t, _ = await trail.create_tenant(
                store, OPS_TENANT_SLUG, OPS_TENANT_NAME,
            )
        return t.tenant_id
    except Exception:  # noqa: BLE001 — race lost, look it up
        existing = await find_by_slug(store, OPS_TENANT_SLUG)
        assert existing is not None
        return existing["tenant_id"]


async def run_job(store, name: str) -> int:
    """Run a registered job. Returns the process exit code."""
    if name not in JOBS:
        logger.error(
            "unknown job: %s (registered: %s)", name, list(JOBS),
        )
        return 2

    job = JOBS[name]
    ops_tenant_id = await _ensure_ops_tenant(store)
    run_id = str(uuid.uuid4())

    lock = Lock(
        store, key=job.advisory_lock_key,
        holder_id=f"runner:{name}:{run_id}",
        ttl_sec=job.max_runtime_sec + 30,
    )
    if not await lock.try_acquire():
        logger.info(
            "job %s: advisory lock held by another runner — exiting 0",
            name,
        )
        return 0

    try:
        await append_via_dispatch(
            store, ops_tenant_id, "JobRunStarted",
            actor=RUNNER_ACTOR,
            payload={
                "job_name": name, "run_id": run_id,
                "schedule": job.schedule,
                "max_runtime_sec": job.max_runtime_sec,
            },
            idempotency_key=f"job:{name}:{run_id}:started",
        )

        error: str | None = None
        ok = False
        try:
            await asyncio.wait_for(
                job.callable(store), timeout=job.max_runtime_sec,
            )
            ok = True
        except asyncio.TimeoutError:
            error = "timeout"
        except Exception as e:  # noqa: BLE001
            error = f"{type(e).__name__}: {e}"

        completed_payload: dict[str, Any] = {
            "job_name": name, "run_id": run_id, "ok": ok,
        }
        if error is not None:
            completed_payload["error"] = error

        await append_via_dispatch(
            store, ops_tenant_id, "JobRunCompleted",
            actor=RUNNER_ACTOR,
            payload=completed_payload,
            idempotency_key=f"job:{name}:{run_id}:completed",
        )
        return 0 if ok else 1
    finally:
        await lock.release()


# --- default job registrations ------------------------------------------


async def _chain_verify_all(store) -> None:
    from app.jobs.chain_verify import verify_all
    await verify_all(store)


async def _timer_sweep_all(store) -> None:
    from app.jobs.timer_sweep import sweep_once
    await sweep_once(store)


register(
    "chain_verify",
    _chain_verify_all,
    schedule="0 * * * *",
    advisory_lock_key=0x636861696E7665,   # ascii 'chainve'
    max_runtime_sec=300.0,
    description="Verify every tenant's hash chain and emit ChainVerified.",
)
register(
    "timer_sweep",
    _timer_sweep_all,
    schedule="* * * * *",
    advisory_lock_key=0x74696D6572726E,   # ascii 'timerrn'
    max_runtime_sec=25.0,
    description="Fire TimerFired for every ready proj_workflow_waits row.",
)
