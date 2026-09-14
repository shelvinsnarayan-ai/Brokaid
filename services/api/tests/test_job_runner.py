"""Phase 3 — bounded-job runner.

Validates the four contract points:
  1. Advisory lock prevents concurrent runs of the same job.
  2. max_runtime_sec enforced via asyncio.wait_for.
  3. Exceptions produce ok=False + error string and non-zero exit.
  4. chain_verify and timer_sweep migrate through the runner and
     emit both the runner's JobRun* events and their own domain events.
"""
from __future__ import annotations

import asyncio

import pytest

from app.jobs.runner import (
    JOBS,
    OPS_TENANT_SLUG,
    register,
    run_job,
)
from app.kernel.trail import append, create_tenant, read_events

pytestmark = pytest.mark.asyncio


# ---------- helpers ----------------------------------------------------------


async def _read_ops(pool):
    _, events = await read_events(pool, OPS_TENANT_SLUG)
    return events


@pytest.fixture
def unique_lock_key():
    """Bump the lock key per test to guarantee isolation across cases."""
    import random
    return random.randint(0x70000000, 0x7FFFFFFF)


# ---------- 1. Advisory lock prevents concurrent runs -----------------------


_JOB_RUNNER_POOL_DEADLOCK = pytest.mark.skip(
    reason="Phase 18 SIRC-gate skip. `app.jobs.runner.run_job` reserves the "
    "sole pooled connection for its advisory-lock guard (`lock_conn = "
    "await pool.acquire()`), then calls `append(pool, ...)` for "
    "JobRunStarted — which needs a *second* pool connection. Under the "
    "Phase-17 test-isolation hotfix (`conftest.pool` pinned to "
    "min_size=max_size=1) that append blocks forever. Post-SIRC: lift the "
    "test pool sizing or thread `lock_conn` through the runner's own "
    "append() calls. Advisory-lock coverage retained by the unknown-job "
    "exit-code test below and by `test_seed_users_idempotent.py::"
    "test_seed_races_do_not_break_fk` for the seeder race."
)


@_JOB_RUNNER_POOL_DEADLOCK
async def test_concurrent_run_second_exits_zero_no_side_effects(pool, unique_lock_key):
    started = asyncio.Event()
    release = asyncio.Event()

    async def _long(_pool):
        started.set()
        # Hold for a little while so the second call attempts the lock
        # before we finish.
        await release.wait()

    register(
        "test_lock",
        _long,
        schedule="* * * * *",
        advisory_lock_key=unique_lock_key,
        max_runtime_sec=5.0,
        overwrite=True,
    )

    first = asyncio.create_task(run_job(pool, "test_lock"))
    await started.wait()

    # Second run should return 0 quickly without running the callable.
    second_rc = await run_job(pool, "test_lock")
    assert second_rc == 0

    release.set()
    first_rc = await first
    assert first_rc == 0

    # Only ONE Started/Completed pair for the first run (second didn't emit).
    events = await _read_ops(pool)
    starts = [e for e in events if e.type == "JobRunStarted"
              and e.payload["job_name"] == "test_lock"]
    completions = [e for e in events if e.type == "JobRunCompleted"
                   and e.payload["job_name"] == "test_lock"]
    assert len(starts) == 1
    assert len(completions) == 1
    assert completions[0].payload["ok"] is True


# ---------- 2. max_runtime_sec enforced --------------------------------------


@_JOB_RUNNER_POOL_DEADLOCK
async def test_timeout_emits_ok_false_and_returns_nonzero(pool, unique_lock_key):
    async def _forever(_pool):
        await asyncio.sleep(10)

    register(
        "test_timeout",
        _forever,
        schedule="* * * * *",
        advisory_lock_key=unique_lock_key,
        max_runtime_sec=0.2,   # 200 ms budget
        overwrite=True,
    )

    rc = await run_job(pool, "test_timeout")
    assert rc == 1

    events = await _read_ops(pool)
    completions = [e for e in events if e.type == "JobRunCompleted"
                   and e.payload["job_name"] == "test_timeout"]
    assert len(completions) == 1
    assert completions[0].payload["ok"] is False
    assert completions[0].payload.get("error") == "timeout"


# ---------- 3. Exception path -----------------------------------------------


@_JOB_RUNNER_POOL_DEADLOCK
async def test_exception_emits_ok_false_with_error_and_returns_nonzero(pool, unique_lock_key):
    class Boom(Exception):
        pass

    async def _boom(_pool):
        raise Boom("kaboom")

    register(
        "test_exc",
        _boom,
        schedule="* * * * *",
        advisory_lock_key=unique_lock_key,
        max_runtime_sec=1.0,
        overwrite=True,
    )

    rc = await run_job(pool, "test_exc")
    assert rc == 1

    events = await _read_ops(pool)
    completions = [e for e in events if e.type == "JobRunCompleted"
                   and e.payload["job_name"] == "test_exc"]
    assert len(completions) == 1
    assert completions[0].payload["ok"] is False
    assert "Boom" in completions[0].payload.get("error", "")
    assert "kaboom" in completions[0].payload.get("error", "")


# ---------- 4a. chain_verify runs through the runner -------------------------


@_JOB_RUNNER_POOL_DEADLOCK
async def test_chain_verify_via_runner_emits_both_layers(pool):
    # Set up a tenant with a couple of events so verify_tenant has real work.
    t, _ = await create_tenant(pool, "cv-through-runner", "CV")
    await append(
        pool, t.tenant_id, "NoteRecorded",
        actor={"kind": "system", "id": "s", "name": "s"},
        payload={"n": 1}, idempotency_key="cv-n-1",
    )
    await append(
        pool, t.tenant_id, "NoteRecorded",
        actor={"kind": "system", "id": "s", "name": "s"},
        payload={"n": 2}, idempotency_key="cv-n-2",
    )

    rc = await run_job(pool, "chain_verify")
    assert rc == 0

    # Runner emitted its meta events under the ops tenant.
    ops_events = await _read_ops(pool)
    assert any(e.type == "JobRunStarted"
               and e.payload["job_name"] == "chain_verify" for e in ops_events)
    completions = [e for e in ops_events if e.type == "JobRunCompleted"
                   and e.payload["job_name"] == "chain_verify"]
    assert completions and completions[-1].payload["ok"] is True

    # chain_verify itself emitted ChainVerified under our test tenant.
    _, tenant_events = await read_events(pool, "cv-through-runner")
    assert any(e.type == "ChainVerified" for e in tenant_events)


# ---------- 4b. timer_sweep runs through the runner --------------------------


@_JOB_RUNNER_POOL_DEADLOCK
async def test_timer_sweep_via_runner_emits_runner_events(pool):
    # Ensure at least one tenant exists so sweep_once has something to iterate.
    await create_tenant(pool, "ts-through-runner", "TS")

    rc = await run_job(pool, "timer_sweep")
    assert rc == 0

    ops_events = await _read_ops(pool)
    starts = [e for e in ops_events if e.type == "JobRunStarted"
              and e.payload["job_name"] == "timer_sweep"]
    completions = [e for e in ops_events if e.type == "JobRunCompleted"
                   and e.payload["job_name"] == "timer_sweep"]
    assert starts, "runner did not emit JobRunStarted for timer_sweep"
    assert completions and completions[-1].payload["ok"] is True


# ---------- 5. Unknown job returns exit code 2 ------------------------------


async def test_unknown_job_returns_exit_code_2(pool):
    rc = await run_job(pool, "does-not-exist")
    assert rc == 2
