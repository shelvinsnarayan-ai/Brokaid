"""Phase 3 — brAIn orchestrator invariants.

Every test injects a resolver so no ambient provider state leaks
across cases. Every mock is deterministic; nothing here can reach a
network — the AnthropicProviderStub seam test proves this explicitly.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest

from app.kernel.brain import (
    Brain,
    BrainSettings,
    PerInvocationBudgetBreached,
    SprintCapBreached,
)
from app.kernel.contracts import (
    Actor,
    ContextEnvelope,
    EventRef,
    InvocationBudget,
)
from app.kernel.providers import AnthropicProviderStub, MockProvider
from app.kernel.trail import create_tenant, read_events

pytestmark = pytest.mark.asyncio


def _envelope(tenant_id: str, *, class_key: str = "structure_risk",
              max_input=4000, max_output=1000, wall_ms=2000) -> ContextEnvelope:
    return ContextEnvelope(
        envelope_id=uuid.uuid4(),
        tenant_id=uuid.UUID(tenant_id),
        matter_id=uuid.uuid4(),
        client_id=uuid.uuid4(),
        class_key=class_key,
        intent="brain-test",
        inputs={"q": "test"},
        context_events=[EventRef(event_id=str(uuid.uuid4()), seq=1)],
        risk_schema_refs=[],
        tenant_config={},
        budget=InvocationBudget(
            max_input_tokens=max_input,
            max_output_tokens=max_output,
            max_wall_ms=wall_ms,
        ),
        caller=Actor(kind="human", id="u1", name="Broker"),
        ts=datetime.now(timezone.utc),
    )


def _fixed_resolver(provider):
    def _r(_name: str):
        return provider
    return _r


async def _events_for(pool, tenant_slug: str):
    _, events = await read_events(pool, tenant_slug)
    return events


# ---------- 1. Success path — 3-event sequence ------------------------------


async def test_success_emits_started_output_completed_in_order(pool):
    tenant, _ = await create_tenant(pool, "brain-ok", "Brain OK")
    env = _envelope(tenant.tenant_id)
    mock = MockProvider(agent_name="mock", mode="success",
                        tokens_per_call=(10, 20))
    brain = Brain(pool, resolver=_fixed_resolver(mock))

    out = await brain.invoke(env, "mock")

    assert out.class_key == env.class_key
    assert out.tokens_used.output == 20

    evs = await _events_for(pool, "brain-ok")
    types_after_genesis = [e.type for e in evs if e.type != "TenantGenesis"]
    assert types_after_genesis == [
        "AgentInvocationStarted",
        "AgentOutputProduced",
        "AgentInvocationCompleted",
    ]
    # envelope_id propagates on every one
    ev_ids = {e.payload.get("envelope_id") for e in evs if e.type != "TenantGenesis"}
    assert ev_ids == {str(env.envelope_id)}
    # Completed carries outcome=ok
    completed = [e for e in evs if e.type == "AgentInvocationCompleted"][0]
    assert completed.payload["outcome"] == "ok"


# ---------- 2. Output token overrun ------------------------------------------


async def test_output_token_overrun_raises_and_no_output_event(pool):
    tenant, _ = await create_tenant(pool, "brain-tok", "Brain Tok")
    env = _envelope(tenant.tenant_id, max_output=100)
    mock = MockProvider(agent_name="mock", mode="overrun_output_tokens",
                        tokens_per_call=(10, 10))  # output overridden by mode
    brain = Brain(pool, resolver=_fixed_resolver(mock))

    with pytest.raises(PerInvocationBudgetBreached):
        await brain.invoke(env, "mock")

    evs = await _events_for(pool, "brain-tok")
    types = [e.type for e in evs if e.type != "TenantGenesis"]
    assert types == [
        "AgentInvocationStarted",
        "AgentInvocationBudgetBreached",
        "AgentInvocationCompleted",
    ]
    breach = [e for e in evs if e.type == "AgentInvocationBudgetBreached"][0]
    assert breach.payload["reason"] == "per_invocation_output"
    # No AgentOutputProduced was emitted.
    assert not any(e.type == "AgentOutputProduced" for e in evs)


# ---------- 3. Wall-ms overrun -----------------------------------------------


async def test_wall_ms_overrun_raises_timeout_and_emits_breach(pool):
    tenant, _ = await create_tenant(pool, "brain-wall", "Brain Wall")
    env = _envelope(tenant.tenant_id, wall_ms=100)  # 0.1s
    mock = MockProvider(agent_name="mock", mode="overrun_wall_ms")
    brain = Brain(pool, resolver=_fixed_resolver(mock))

    with pytest.raises(asyncio.TimeoutError):
        await brain.invoke(env, "mock")

    evs = await _events_for(pool, "brain-wall")
    breach_events = [e for e in evs if e.type == "AgentInvocationBudgetBreached"]
    assert len(breach_events) == 1
    assert breach_events[0].payload["reason"] == "wall_ms"
    assert not any(e.type == "AgentOutputProduced" for e in evs)


# ---------- 4. Invalid schema drives confidence to (0,0) --------------------


async def test_invalid_schema_zeros_out_schema_validity_and_class_agreement(pool):
    tenant, _ = await create_tenant(pool, "brain-inv", "Brain Inv")
    env = _envelope(tenant.tenant_id)
    mock = MockProvider(agent_name="mock", mode="invalid_schema",
                        tokens_per_call=(5, 5))
    brain = Brain(pool, resolver=_fixed_resolver(mock))

    await brain.invoke(env, "mock")

    evs = await _events_for(pool, "brain-inv")
    out_ev = [e for e in evs if e.type == "AgentOutputProduced"][0]
    comps = out_ev.payload["confidence"]["components"]
    assert comps["schema_validity"] == 0.0
    assert comps["class_agreement"] == 0.0


# ---------- 5. Confidence components always present -------------------------


async def test_confidence_components_present_on_every_output_event(pool):
    tenant, _ = await create_tenant(pool, "brain-conf", "Brain Conf")
    env = _envelope(tenant.tenant_id)
    mock = MockProvider(agent_name="mock", mode="success",
                        tokens_per_call=(5, 5))
    brain = Brain(pool, resolver=_fixed_resolver(mock))

    await brain.invoke(env, "mock")

    evs = await _events_for(pool, "brain-conf")
    out_ev = [e for e in evs if e.type == "AgentOutputProduced"][0]
    comps = out_ev.payload["confidence"]["components"]
    assert set(comps.keys()) == {
        "schema_validity", "completeness",
        "citation_existence", "class_agreement",
    }
    assert 0.0 <= out_ev.payload["confidence"]["scalar"] <= 1.0


# ---------- 6. Sprint cap enforcement ---------------------------------------


async def test_sprint_cap_alerts_at_50_80_100_then_breach(pool):
    tenant, _ = await create_tenant(pool, "brain-cap", "Brain Cap")
    # cap = 100 tokens total; each success invocation consumes 10 (5 in + 5 out).
    settings = BrainSettings(sprint_cap_tokens=100)
    mock = MockProvider(agent_name="mock", mode="success",
                        tokens_per_call=(5, 5))
    brain = Brain(pool, resolver=_fixed_resolver(mock), settings=settings)

    # 10 invocations should exactly hit 100 tokens.
    for _ in range(10):
        await brain.invoke(_envelope(tenant.tenant_id), "mock")

    evs = await _events_for(pool, "brain-cap")
    alerts = [e for e in evs if e.type == "SprintCapAlert"]
    thresholds = sorted(a.payload["threshold_pct"] for a in alerts)
    assert thresholds == [50, 80, 100], f"got {thresholds}"

    # Next invocation must be blocked.
    with pytest.raises(SprintCapBreached):
        await brain.invoke(_envelope(tenant.tenant_id), "mock")

    evs = await _events_for(pool, "brain-cap")
    breaches = [e for e in evs
                if e.type == "AgentInvocationBudgetBreached"
                and e.payload.get("reason") == "sprint_cap"]
    assert len(breaches) == 1


async def test_sprint_cap_waived_but_usage_tracked(pool):
    tenant, _ = await create_tenant(pool, "brain-waived", "Brain Waived")
    settings = BrainSettings(sprint_cap_tokens=None)   # explicit None
    mock = MockProvider(agent_name="mock", mode="success",
                        tokens_per_call=(7, 3))
    brain = Brain(pool, resolver=_fixed_resolver(mock), settings=settings)

    for _ in range(5):
        await brain.invoke(_envelope(tenant.tenant_id), "mock")

    evs = await _events_for(pool, "brain-waived")
    # No alerts and no breach events.
    assert not any(e.type == "SprintCapAlert" for e in evs)
    assert not any(e.type == "AgentInvocationBudgetBreached" for e in evs)

    # But the projection tracks 5 * 10 = 50 tokens and 5 invocations.
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)",
                            tenant.tenant_id)
            row = await c.fetchrow(
                "SELECT tokens_used, invocations FROM proj_sprint_usage "
                "WHERE tenant_id = $1::uuid",
                tenant.tenant_id,
            )
    assert row["tokens_used"] == 50
    assert row["invocations"] == 5


# ---------- 7. Provider seam — stub raises NotImplementedError --------------


async def test_provider_seam_anthropic_stub_raises_no_llm_call(pool):
    tenant, _ = await create_tenant(pool, "brain-stub", "Brain Stub")
    env = _envelope(tenant.tenant_id)
    stub = AnthropicProviderStub()
    brain = Brain(pool, resolver=_fixed_resolver(stub))

    with pytest.raises(NotImplementedError):
        await brain.invoke(env, "anthropic-stub")

    # An error outcome is recorded.
    evs = await _events_for(pool, "brain-stub")
    completed = [e for e in evs if e.type == "AgentInvocationCompleted"]
    assert len(completed) == 1
    assert completed[0].payload["outcome"] == "error"
    # And no AgentOutputProduced event was emitted.
    assert not any(e.type == "AgentOutputProduced" for e in evs)
