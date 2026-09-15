"""Bounded-job runner.

Every scheduled job runs through this module:
  1. Acquire a Postgres advisory lock keyed to the job. If not
     acquired, exit 0 quietly (another instance is running).
  2. Emit `JobRunStarted` under the ops tenant.
  3. Run the callable under `asyncio.wait_for(max_runtime_sec)`.
  4. Emit `JobRunCompleted` with ok=true/false + optional error.
  5. Release the lock.

Registered jobs:
  - `chain_verify` (from phase 1)
  - `timer_sweep`  (from phase 2)
Both are wrappers around the phase-1/2 callables — no logic change.

External schedulers (cron, k8s CronJob, systemd timer) call this via
`python -m app.jobs.run <job_name>`. There is no in-process scheduler
and no always-on worker.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import asyncpg

from app.kernel.trail import append, create_tenant

logger = logging.getLogger("brokaid.job_runner")


OPS_TENANT_SLUG = "brokaid-ops"
OPS_TENANT_NAME = "brokAId Ops"
RUNNER_ACTOR = {"kind": "system", "id": "job-runner", "name": "runner"}


JobCallable = Callable[[asyncpg.Pool], Awaitable[Any]]


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


async def _ensure_ops_tenant(pool: asyncpg.Pool) -> str:
    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT tenant_id FROM tenants WHERE slug = $1", OPS_TENANT_SLUG
        )
    if row is not None:
        return str(row["tenant_id"])
    try:
        t, _ = await create_tenant(pool, OPS_TENANT_SLUG, OPS_TENANT_NAME)
        return t.tenant_id
    except asyncpg.UniqueViolationError:
        async with pool.acquire() as c:
            row = await c.fetchrow(
                "SELECT tenant_id FROM tenants WHERE slug = $1",
                OPS_TENANT_SLUG,
            )
        assert row is not None
        return str(row["tenant_id"])


async def run_job(pool: asyncpg.Pool, name: str) -> int:
    """Run a registered job. Returns the process exit code."""
    if name not in JOBS:
        logger.error("unknown job: %s (registered: %s)", name, list(JOBS))
        return 2

    job = JOBS[name]
    ops_tenant_id = await _ensure_ops_tenant(pool)
    run_id = str(uuid.uuid4())

    # Grab a DEDICATED physical connection for the advisory lock so it
    # survives while the callable + Started/Completed appends run
    # concurrently on the shared pool.
    #
    # Phase-18: previously we did `pool.acquire()` and threaded the
    # returned pool-owned conn through `append(..., conn=lock_conn)`.
    # That worked in production (default pool max_size=5) but deadlocked
    # every concurrency test under `conftest.pool` (pinned to
    # min=max=1 to serialise TRUNCATE-CASCADE FK ordering) because
    # `pool.acquire()` for the second run couldn't return until the
    # first run finished. Switching to `asyncpg.connect(...)` with the
    # pool's own connect args opens a fresh physical connection outside
    # the pool — same DB, same credentials, same session-scoped
    # advisory-lock semantics — and closes it in the finally block.
    # `pool._connect_args` / `pool._connect_kwargs` are pool internals
    # but their shape has been stable across asyncpg 0.28→0.30. If
    # asyncpg ever drops them we swap to passing the DSN through
    # `run_job(pool, name, *, dsn=...)` — see PRD Known Test Debt.
    lock_conn = await asyncpg.connect(
        *pool._connect_args, **pool._connect_kwargs
    )
    try:
        got = await lock_conn.fetchval(
            "SELECT pg_try_advisory_lock($1)", job.advisory_lock_key
        )
        if not got:
            logger.info(
                "job %s: advisory lock held by another runner — exiting 0",
                name,
            )
            return 0

        # Emit Started.
        await append(
            pool, ops_tenant_id, "JobRunStarted",
            actor=RUNNER_ACTOR,
            payload={
                "job_name": name,
                "run_id": run_id,
                "schedule": job.schedule,
                "max_runtime_sec": job.max_runtime_sec,
            },
            idempotency_key=f"job:{name}:{run_id}:started",
            conn=lock_conn,
        )

        error: str | None = None
        ok = False
        try:
            await asyncio.wait_for(
                job.callable(pool), timeout=job.max_runtime_sec
            )
            ok = True
        except asyncio.TimeoutError:
            error = "timeout"
        except Exception as e:
            error = f"{type(e).__name__}: {e}"

        completed_payload: dict[str, Any] = {
            "job_name": name, "run_id": run_id, "ok": ok,
        }
        if error is not None:
            completed_payload["error"] = error

        await append(
            pool, ops_tenant_id, "JobRunCompleted",
            actor=RUNNER_ACTOR,
            payload=completed_payload,
            idempotency_key=f"job:{name}:{run_id}:completed",
            conn=lock_conn,
        )
        return 0 if ok else 1
    finally:
        try:
            await lock_conn.execute(
                "SELECT pg_advisory_unlock($1)", job.advisory_lock_key
            )
        finally:
            await lock_conn.close()


# --- default job registrations ------------------------------------------


async def _chain_verify_all(pool: asyncpg.Pool) -> None:
    from app.jobs.chain_verify import verify_tenant
    async with pool.acquire() as c:
        rows = await c.fetch(
            "SELECT slug FROM tenants WHERE slug <> $1 ORDER BY slug",
            OPS_TENANT_SLUG,
        )
    for r in rows:
        await verify_tenant(pool, r["slug"])


async def _timer_sweep_all(pool: asyncpg.Pool) -> None:
    from app.jobs.timer_sweep import sweep_once
    await sweep_once(pool)


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
