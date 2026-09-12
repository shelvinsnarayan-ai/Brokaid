"""Kill-and-resume test — SIGKILL a subprocess mid-workflow, then resume.

The workflow is a chain of 5 `WorkflowStep` events with deterministic
idempotency_keys `wf:{workflow_id}:{step}`. Duplicate emits are no-ops.
"""
from __future__ import annotations

import asyncio
import multiprocessing as mp
import os
import signal
import time
import uuid

import pytest

from app.kernel.gate.workflow import emit_step, last_step, resume
from app.kernel.trail import create_tenant, read_events

pytestmark = pytest.mark.asyncio


def _child_target(dsn: str, tenant_id: str, workflow_id: str):
    """Runs in a fresh subprocess: emit step 4 (after sleep) then step 5."""
    import asyncio
    import asyncpg

    async def go():
        pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=2)
        try:
            time.sleep(0.5)
            await emit_step(pool, tenant_id, workflow_id, 4)
            time.sleep(5)   # give parent time to SIGKILL
            await emit_step(pool, tenant_id, workflow_id, 5)
        finally:
            await pool.close()

    asyncio.run(go())


async def test_kill_and_resume(pool):
    tenant, _ = await create_tenant(pool, slug="kar", display_name="Kill & Resume")
    workflow_id = str(uuid.uuid4())

    # Steps 1-3 on the main process.
    for i in range(1, 4):
        await emit_step(pool, tenant.tenant_id, workflow_id, i)

    # Subprocess starts step 4/5, we SIGKILL after step 4 lands.
    dsn = "postgresql://brokaid:brokaid_dev_local@localhost:5432/brokaid_test"
    ctx = mp.get_context("spawn")
    proc = ctx.Process(target=_child_target, args=(dsn, tenant.tenant_id, workflow_id))
    proc.start()

    # Wait for step 4 to appear.
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        s = await last_step(pool, tenant.tenant_id, workflow_id)
        if s >= 4:
            break
        await asyncio.sleep(0.1)

    # SIGKILL the subprocess mid-workflow.
    os.kill(proc.pid, signal.SIGKILL)
    proc.join(timeout=5)
    assert proc.exitcode is not None

    # Resume from the main process.
    emitted = await resume(pool, tenant.tenant_id, workflow_id, total_steps=5)

    # Terminal state: exactly steps 1..5, one event each.
    _, events = await read_events(pool, "kar")
    step_events = [e for e in events if e.type == "WorkflowStep"
                   and e.payload.get("workflow_id") == workflow_id]
    steps_seen = sorted(int(e.payload["step"]) for e in step_events)
    assert steps_seen == [1, 2, 3, 4, 5], (
        f"expected [1..5], got {steps_seen}. "
        f"Duplicate side-effect events would show up here."
    )
    # Each step has a unique idempotency_key.
    idem = [e.idempotency_key for e in step_events]
    assert len(idem) == len(set(idem))
    assert emitted <= 2
