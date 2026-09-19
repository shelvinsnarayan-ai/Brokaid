"""Phase R5c-1a — brAIn dispatch conversion pins.

Extract-first pins on the R5c-1a Brain conversion:

- Brain(store) accepts either an pool or a Motor DB /
  TenantSafeDatabase. Positional signature preserved so pre-rewire
  Brain(pool) callers keep working.
- brain.pool attribute preserved as backward-compat alias.
- 3-event happy-path parity across backends (matched overrides at
  trail primitive level — Brain does not accept ts_override on its
  emit surface; the extraction guarantee is the shape/actor/idem +
  the dispatch, not the timestamp).
- Sprint-cap breach event parity across backends.
- Per-invocation-output breach event parity across backends.
- brain_mongo.read_sprint returns correct (tokens_used,
  last_alert_pct) after AgentOutputProduced projection lands.
- Brain(pool).invoke() on PG lands the 3 events.
- Brain(mongo_db).invoke() on Mongo lands the 3 events.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.kernel.brain import Brain
from app.kernel.contracts.models import (
    Actor, ContextEnvelope, InvocationBudget, OutputContract, TokenCount,
)

pytestmark = pytest.mark.asyncio


class _MockProvider:
    def __init__(self, name: str, class_key: str = "structure_risk"):
        self.agent_name = name
        self._class_key = class_key

    async def run(self, envelope: ContextEnvelope) -> OutputContract:
        return OutputContract(
            envelope_id=envelope.envelope_id,
            class_key=self._class_key,
            decision="propose",
            payload={"agent": self.agent_name},
            citations=[],
            notes="r5c1a-pin",
            tokens_used=TokenCount(input=10, output=5),
            wall_ms=1,
            provider_meta={"mocked": True, "model_id": "r5c1a-pin"},
        )


def _envelope(tenant_id: str) -> ContextEnvelope:
    return ContextEnvelope(
        envelope_id=uuid.uuid4(),
        tenant_id=uuid.UUID(tenant_id),
        matter_id=uuid.uuid4(),
        client_id=uuid.uuid4(),
        class_key="structure_risk",
        intent="r5c1a-pin",
        inputs={},
        context_events=[],
        risk_schema_refs=[],
        tenant_config={},
        budget=InvocationBudget(
            max_input_tokens=4000, max_output_tokens=1000, max_wall_ms=5000,
        ),
        caller=Actor(kind="system", id="pin", name="pin"),
        ts=datetime.now(timezone.utc),
    )


async def _pg_tenant(pool):
    from app.kernel import trail
    slug = f"brn-{uuid.uuid4().hex[:6]}"
    t, _ = await trail.create_tenant(pool, slug=slug, display_name=slug)
    return t.tenant_id


async def _seed_matched_tenant(pool, mongo_db):
    """Create the SAME tenant with matched overrides on both stores so
    parity pins land under a common tenant + prev_hash."""
    from app.kernel import trail, trail_mongo
    from app.kernel.tenant_ctx import use_tenant
    slug = f"br-{uuid.uuid4().hex[:6]}"
    frozen_tid = str(uuid.uuid4())
    frozen_gen_ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    frozen_gen_eid = str(uuid.uuid4())
    await trail.create_tenant(
        pool, slug=slug, display_name=slug,
        tenant_id_override=frozen_tid,
        ts_override=frozen_gen_ts, event_id_override=frozen_gen_eid,
    )
    async with use_tenant(frozen_tid):
        await trail_mongo.create_tenant(
            mongo_db, slug, slug,
            tenant_id_override=frozen_tid,
            ts_override=frozen_gen_ts, event_id_override=frozen_gen_eid,
        )
    return frozen_tid


# ---- Pin 1: PG happy path — 3 events land ----------------------------
async def test_r5c1a_brain_invoke_pg_lands_3_events(pool):
    tid = await _pg_tenant(pool)
    provider = _MockProvider("intake")
    brain = Brain(pool, resolver=lambda name: provider)
    await brain.invoke(_envelope(tid), "intake")

    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tid,
            )
            rows = await c.fetch(
                "SELECT type FROM events "
                " WHERE tenant_id=$1::uuid "
                "   AND actor->>'id' = 'brain' "
                " ORDER BY seq ASC",
                tid,
            )
    types = [r["type"] for r in rows]
    assert types == [
        "AgentInvocationStarted",
        "AgentOutputProduced",
        "AgentInvocationCompleted",
    ]


# ---- Pin 2: Mongo happy path — 3 events land -------------------------
async def test_r5c1a_brain_invoke_mongo_lands_3_events(pool, mongo_db):
    from app.db.tenant_safe import wrap
    from app.kernel.tenant_ctx import use_tenant

    frozen_tid = await _seed_matched_tenant(pool, mongo_db)
    provider = _MockProvider("intake")
    brain = Brain(mongo_db, resolver=lambda name: provider)
    await brain.invoke(_envelope(frozen_tid), "intake")

    safe = wrap(mongo_db)
    types = []
    async with use_tenant(frozen_tid):
        cursor = safe.events.find({"actor.id": "brain"}).sort("seq", 1)
        async for r in cursor:
            types.append(r["type"])
    assert types == [
        "AgentInvocationStarted",
        "AgentOutputProduced",
        "AgentInvocationCompleted",
    ]


# ---- Pin 3: 3-event happy-path parity across backends ----------------
async def test_r5c1a_brain_happy_path_parity(pool, mongo_db):
    """Direct emit of the 3 AgentInvocation happy-path events on both
    stores with matched overrides. Byte-identical event_hash on each."""
    from app.kernel import trail, trail_mongo
    from app.kernel.tenant_ctx import use_tenant

    frozen_tid = await _seed_matched_tenant(pool, mongo_db)

    envelope_id = str(uuid.uuid4())
    agent_name = "intake"
    ts_base = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    event_eids = [str(uuid.uuid4()) for _ in range(3)]
    actor = {"kind": "system", "id": "brain", "name": "brain"}
    events = [
        (
            "AgentInvocationStarted",
            {"envelope_id": envelope_id, "agent_name": agent_name,
             "class_key": "structure_risk",
             "budget": {"max_input_tokens": 4000, "max_output_tokens": 1000,
                        "max_wall_ms": 5000}},
            f"aginv:start:{envelope_id}:{agent_name}",
        ),
        (
            "AgentOutputProduced",
            {"envelope_id": envelope_id, "agent_name": agent_name,
             "class_key": "structure_risk", "decision": "propose",
             "citations": [], "tokens_used": {"input": 10, "output": 5},
             "wall_ms": 1,
             "confidence": {"components": {}, "scalar": 0.5},
             "flags": {"input_tokens_overrun": False}},
            f"aginv:output:{envelope_id}:{agent_name}",
        ),
        (
            "AgentInvocationCompleted",
            {"envelope_id": envelope_id, "agent_name": agent_name,
             "outcome": "ok", "attempt_count": 1,
             "acceptance_mode": False},
            f"aginv:done:{envelope_id}:{agent_name}",
        ),
    ]

    for i, (typ, payload, idem) in enumerate(events):
        ts = ts_base + timedelta(seconds=i + 1)
        eid = event_eids[i]
        pg_ev = await trail.append(
            pool, frozen_tid, typ, actor=actor, payload=payload,
            idempotency_key=idem,
            ts_override=ts, event_id_override=eid,
        )
        async with use_tenant(frozen_tid):
            mo_ev = await trail_mongo.append_event(
                mongo_db, frozen_tid, typ, actor=actor, payload=payload,
                idempotency_key=idem,
                ts_override=ts, event_id_override=eid,
            )
        assert pg_ev.event_hash == mo_ev.event_hash, (
            f"{typ} drift: pg={pg_ev.event_hash} mo={mo_ev.event_hash}"
        )


# ---- Pin 4: SprintCapBreached event parity ---------------------------
async def test_r5c1a_brain_sprint_cap_breach_parity(pool, mongo_db):
    from app.kernel import trail, trail_mongo
    from app.kernel.tenant_ctx import use_tenant

    frozen_tid = await _seed_matched_tenant(pool, mongo_db)

    envelope_id = str(uuid.uuid4())
    actor = {"kind": "system", "id": "brain", "name": "brain"}
    payload = {
        "envelope_id": envelope_id, "agent_name": "intake",
        "reason": "sprint_cap",
        "detail": {"current": 100, "cap": 100},
    }
    idem = f"aginv:breach:{envelope_id}:sprint_cap"
    ts = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    eid = str(uuid.uuid4())

    pg_ev = await trail.append(
        pool, frozen_tid, "AgentInvocationBudgetBreached",
        actor=actor, payload=payload, idempotency_key=idem,
        ts_override=ts, event_id_override=eid,
    )
    async with use_tenant(frozen_tid):
        mo_ev = await trail_mongo.append_event(
            mongo_db, frozen_tid, "AgentInvocationBudgetBreached",
            actor=actor, payload=payload, idempotency_key=idem,
            ts_override=ts, event_id_override=eid,
        )
    assert pg_ev.event_hash == mo_ev.event_hash


# ---- Pin 5: Per-invocation output-tokens breach event parity ---------
async def test_r5c1a_brain_per_invocation_breach_parity(pool, mongo_db):
    from app.kernel import trail, trail_mongo
    from app.kernel.tenant_ctx import use_tenant

    frozen_tid = await _seed_matched_tenant(pool, mongo_db)

    envelope_id = str(uuid.uuid4())
    actor = {"kind": "system", "id": "brain", "name": "brain"}
    payload = {
        "envelope_id": envelope_id, "agent_name": "intake",
        "reason": "per_invocation_output",
        "detail": {"budget_output_tokens": 1000, "used_output_tokens": 5000},
    }
    idem = f"aginv:breach:{envelope_id}:per_invocation_output"
    ts = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    eid = str(uuid.uuid4())

    pg_ev = await trail.append(
        pool, frozen_tid, "AgentInvocationBudgetBreached",
        actor=actor, payload=payload, idempotency_key=idem,
        ts_override=ts, event_id_override=eid,
    )
    async with use_tenant(frozen_tid):
        mo_ev = await trail_mongo.append_event(
            mongo_db, frozen_tid, "AgentInvocationBudgetBreached",
            actor=actor, payload=payload, idempotency_key=idem,
            ts_override=ts, event_id_override=eid,
        )
    assert pg_ev.event_hash == mo_ev.event_hash


# ---- Pin 6: brain_mongo.read_sprint returns correct values -----------
async def test_r5c1a_brain_mongo_read_sprint(pool, mongo_db):
    """After a Brain.invoke() cycle on Mongo, proj_sprint_usage has
    been populated by workflow_mongo.project_event. read_sprint
    returns the tokens from that projection."""
    from app.kernel import brain_mongo

    frozen_tid = await _seed_matched_tenant(pool, mongo_db)

    # Baseline: no projection row → (0, 0).
    used, alert = await brain_mongo.read_sprint(mongo_db, frozen_tid)
    assert (used, alert) == (0, 0)

    # After a Brain.invoke() the AgentOutputProduced projection lands.
    provider = _MockProvider("intake")
    brain = Brain(mongo_db, resolver=lambda name: provider)
    await brain.invoke(_envelope(frozen_tid), "intake")

    used, alert = await brain_mongo.read_sprint(mongo_db, frozen_tid)
    # provider tokens_used = {"input": 10, "output": 5} → 15 total.
    assert used == 15
    assert alert == 0


# ---- Pin 7: brain.pool backward-compat attribute ---------------------
async def test_r5c1a_brain_pool_backward_compat_attr(pool):
    """Pre-rewire callers may read `brain.pool`. Preserve that alias."""
    b = Brain(pool)
    assert b.pool is pool
    assert b.store is pool
