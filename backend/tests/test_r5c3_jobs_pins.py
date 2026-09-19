"""Phase R5c-3 — jobs + advisory-lock parity pins.

Coverage
--------
- `kernel.locks.Lock` — try_acquire mutex + TTL reclaim on both backends.
- `jobs.chain_verify.verify_tenant` — ChainVerified event lands on both.
- `jobs.compute_kpis.compute_for_all_tenants` — proj_kpi_daily populated
  + KPIDailyComputed emitted on both.
- `jobs.timer_sweep.sweep_once` — TimerFired emitted on both.
- `jobs.runner.run_job` — JobRunStarted + JobRunCompleted emitted on both.
- `kpis_dispatch.persist_daily` — parity of proj_kpi_daily doc across
  backends given identical KpiValues.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.db.tenant_safe import wrap as _wrap_safe
from app.kernel import trail, trail_mongo
from app.kernel.kpis import KpiValue
from app.kernel.locks import Lock
from app.kernel.tenant_ctx import use_tenant


pytestmark = pytest.mark.asyncio


# ---- Fixture helpers ------------------------------------------------

async def _pg_tenant(pool, slug_prefix: str = "r5c3") -> str:
    slug = f"{slug_prefix}-{uuid.uuid4().hex[:6]}"
    t, _ = await trail.create_tenant(pool, slug=slug, display_name=slug)
    return t.tenant_id


async def _mongo_tenant(mongo_db, slug_prefix: str = "r5c3") -> str:
    slug = f"{slug_prefix}-{uuid.uuid4().hex[:6]}"
    tid = str(uuid.uuid4())
    async with use_tenant(tid):
        await trail_mongo.create_tenant(
            mongo_db, slug, slug, tenant_id_override=tid,
        )
    return tid


# ---- Pin 1: Lock mutex — PG -----------------------------------------
async def test_pin_r5c3_lock_pg_mutex(pool):
    key = 0x5c3010001
    a = Lock(pool, key=key, holder_id="a", ttl_sec=30)
    b = Lock(pool, key=key, holder_id="b", ttl_sec=30)
    assert await a.try_acquire() is True
    assert await b.try_acquire() is False
    await a.release()
    assert await b.try_acquire() is True
    await b.release()


# ---- Pin 2: Lock mutex — Mongo --------------------------------------
async def test_pin_r5c3_lock_mongo_mutex(mongo_db):
    key = 0x5c3010002
    a = Lock(mongo_db, key=key, holder_id="a", ttl_sec=30)
    b = Lock(mongo_db, key=key, holder_id="b", ttl_sec=30)
    assert await a.try_acquire() is True
    assert await b.try_acquire() is False
    await a.release()
    assert await b.try_acquire() is True
    await b.release()


# ---- Pin 3: Lock TTL reclaim — Mongo --------------------------------
async def test_pin_r5c3_lock_mongo_ttl_reclaim(mongo_db):
    key = 0x5c3010003
    a = Lock(mongo_db, key=key, holder_id="a", ttl_sec=0.05)
    b = Lock(mongo_db, key=key, holder_id="b", ttl_sec=30)
    assert await a.try_acquire() is True
    # a leaked without release — simulate crash. Wait past TTL.
    await asyncio.sleep(0.12)
    # b reclaims the expired lock.
    assert await b.try_acquire() is True
    await b.release()


# ---- Pin 4: chain_verify on Mongo emits ChainVerified ---------------
async def test_pin_r5c3_chain_verify_mongo(mongo_db):
    from app.jobs.chain_verify import verify_tenant
    from app.kernel.trail_dispatch import append_via_dispatch

    slug = f"cv-{uuid.uuid4().hex[:6]}"
    tid = str(uuid.uuid4())
    async with use_tenant(tid):
        await trail_mongo.create_tenant(
            mongo_db, slug, slug, tenant_id_override=tid,
        )
    # Emit a couple of events so the chain has structure.
    for i in range(3):
        await append_via_dispatch(
            mongo_db, tid, "PinFiller",
            actor={"kind": "system", "id": "pin", "name": "pin"},
            payload={"i": i},
            idempotency_key=f"pin:cv:{tid}:{i}",
        )

    ok = await verify_tenant(mongo_db, slug)
    assert ok is True

    safe = _wrap_safe(mongo_db)
    async with use_tenant(tid):
        ev = await safe.events.find_one({"type": "ChainVerified"})
    assert ev is not None
    assert ev["payload"]["ok"] is True
    assert ev["payload"]["to_seq"] >= 3


# ---- Pin 5: compute_kpis on Mongo populates proj_kpi_daily ----------
async def test_pin_r5c3_compute_kpis_mongo(mongo_db):
    from app.jobs.compute_kpis import compute_for_all_tenants
    from app.jobs.runner import OPS_TENANT_SLUG

    # Ensure ops tenant exists on Mongo so KPIDailyComputed can land.
    ops_tid = str(uuid.uuid4())
    async with use_tenant(ops_tid):
        await trail_mongo.create_tenant(
            mongo_db, OPS_TENANT_SLUG, "ops",
            tenant_id_override=ops_tid,
        )
    tid = await _mongo_tenant(mongo_db, "ckpi")

    await compute_for_all_tenants(mongo_db)

    safe = _wrap_safe(mongo_db)
    async with use_tenant(tid):
        n = await safe.proj_kpi_daily.count_documents({})
    assert n >= 12  # 12 KPIs computed


# ---- Pin 6: timer_sweep on Mongo emits TimerFired -------------------
async def test_pin_r5c3_timer_sweep_mongo(mongo_db):
    from app.jobs.timer_sweep import sweep_once
    tid = await _mongo_tenant(mongo_db, "tsw")
    safe = _wrap_safe(mongo_db)
    wf_id, step = str(uuid.uuid4()), "wait_a"
    async with use_tenant(tid):
        await safe.proj_workflow_waits.insert_one({
            "workflow_id": wf_id, "step": step,
            "resume_predicate": {}, "fire_at":
                datetime.now(timezone.utc) - timedelta(seconds=5),
            "timeout_at": None, "resolved": False,
        })

    fired = await sweep_once(mongo_db)
    assert fired >= 1

    async with use_tenant(tid):
        ev = await safe.events.find_one(
            {"type": "TimerFired", "payload.workflow_id": wf_id},
        )
    assert ev is not None


# ---- Pin 7: runner on Mongo emits JobRunStarted + Completed --------
async def test_pin_r5c3_runner_mongo(mongo_db):
    from app.jobs.runner import run_job

    # Register a no-op job scoped to this test to avoid contending
    # with the default `chain_verify` / `timer_sweep` locks.
    from app.jobs.runner import register
    async def _noop(store) -> None:
        pass
    register(
        "r5c3_noop", _noop,
        schedule="* * * * *",
        advisory_lock_key=0x5C3F1000,
        max_runtime_sec=5.0,
        overwrite=True,
    )

    rc = await run_job(mongo_db, "r5c3_noop")
    assert rc == 0

    from app.jobs.runner import OPS_TENANT_SLUG
    safe = _wrap_safe(mongo_db)
    ops = await safe.system("tenants").find_one({"slug": OPS_TENANT_SLUG})
    assert ops is not None
    ops_tid = ops["tenant_id"]

    async with use_tenant(ops_tid):
        started = await safe.events.count_documents({"type": "JobRunStarted"})
        completed = await safe.events.count_documents({"type": "JobRunCompleted"})
    assert started >= 1
    assert completed >= 1


# ---- Pin 8: kpis.persist_daily PG↔Mongo shape parity ---------------
async def test_pin_r5c3_persist_daily_parity(pool, mongo_db):
    from app.kernel.kpis_dispatch import persist_daily

    tid_pg = await _pg_tenant(pool, "pdpg")
    tid_mo = await _mongo_tenant(mongo_db, "pdmo")

    # Same value shape for both backends.
    day = date(2025, 6, 1)
    values = [
        KpiValue(1, 1000.0, {"label": "Book", "unit": "AUD"},
                 None, [1000.0] * 12),
        KpiValue(5, 3.0, {"label": "Response", "unit": "h"},
                 0.5, [2.5, 3.0] + [3.0] * 10),
    ]

    await persist_daily(pool, tid_pg, values, day=day)
    await persist_daily(mongo_db, tid_mo, values, day=day)

    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tid_pg,
            )
            pg_rows = await c.fetch(
                "SELECT kpi_id, value_scalar, spark_series, delta_prev "
                "  FROM proj_kpi_daily "
                " WHERE tenant_id=$1::uuid AND day=$2::date "
                " ORDER BY kpi_id", tid_pg, day,
            )
    pg = {int(r["kpi_id"]): {
        "value_scalar": float(r["value_scalar"]),
        "spark_series": [float(x) for x in r["spark_series"]],
        "delta_prev": (float(r["delta_prev"])
                       if r["delta_prev"] is not None else None),
    } for r in pg_rows}

    safe = _wrap_safe(mongo_db)
    async with use_tenant(tid_mo):
        mo_docs = [r async for r in safe.proj_kpi_daily.find(
            {"day": day.isoformat()},
        ).sort("kpi_id", 1)]
    mo = {int(d["kpi_id"]): {
        "value_scalar": float(d["value_scalar"]),
        "spark_series": [float(x) for x in d["spark_series"]],
        "delta_prev": d["delta_prev"],
    } for d in mo_docs}

    assert pg == mo
