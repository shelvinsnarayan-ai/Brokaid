"""Phase 16 — brAIn transient-error retry cap tests.

Default retry cap: 2 attempts (i.e. 1 retry).
`BROKAID_ACCEPTANCE_MODE=true` → 1 attempt (no retry).

Tests:
  * default env: transient failure once → success on retry, attempt_count=2.
  * default env: two transient failures → raises, attempt_count=2.
  * acceptance mode env: one transient failure → raises immediately,
    attempt_count=1.
  * non-transient failure (4xx-style) → no retry regardless of env.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone

import pytest

from app.kernel.brain import Brain
from app.kernel.contracts import Actor, ContextEnvelope, EventRef, InvocationBudget
from app.kernel.trail import create_tenant, read_events


pytestmark = pytest.mark.asyncio


def _envelope(tenant_id: str) -> ContextEnvelope:
    return ContextEnvelope(
        envelope_id=uuid.uuid4(),
        tenant_id=uuid.UUID(tenant_id),
        matter_id=uuid.uuid4(),
        client_id=uuid.uuid4(),
        class_key="structure_risk",
        intent="retry-test",
        inputs={"q": "test"},
        context_events=[EventRef(event_id=str(uuid.uuid4()), seq=1)],
        risk_schema_refs=[],
        tenant_config={},
        budget=InvocationBudget(
            max_input_tokens=4000, max_output_tokens=1000, max_wall_ms=5000,
        ),
        caller=Actor(kind="human", id="u1", name="Broker"),
        ts=datetime.now(timezone.utc),
    )


class _TransientThenSuccessProvider:
    agent_name = "flaky"

    def __init__(self, *, fail_times: int):
        self._left = fail_times
        self.calls = 0

    async def run(self, envelope):
        self.calls += 1
        if self._left > 0:
            self._left -= 1
            raise ConnectionResetError("transient boom")
        # Success — mimic MockProvider(success)
        from app.kernel.contracts.models import OutputContract, TokenCount
        return OutputContract(
            envelope_id=envelope.envelope_id,
            class_key=envelope.class_key,
            decision="propose",
            payload={"ok": True},
            citations=[],
            notes="ok",
            tokens_used=TokenCount(input=5, output=5),
            wall_ms=1,
            provider_meta={"mocked": True, "model_id": "fake"},
        )


class _AlwaysNonTransient:
    agent_name = "fatal"

    async def run(self, envelope):
        raise ValueError("400-style contract violation")


def _resolver(prov):
    return lambda _name: prov


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("BROKAID_ACCEPTANCE_MODE", raising=False)
    monkeypatch.delenv("BROKAID_INVOKE_MAX_ATTEMPTS", raising=False)


async def _events_for(pool, slug):
    _, events = await read_events(pool, slug)
    return events


async def test_default_env_one_transient_failure_is_retried(pool):
    tenant, _ = await create_tenant(pool, "retry-ok", "Retry OK")
    prov = _TransientThenSuccessProvider(fail_times=1)
    brain = Brain(pool, resolver=_resolver(prov))
    out = await brain.invoke(_envelope(tenant.tenant_id), "flaky")
    assert out.payload == {"ok": True}
    assert prov.calls == 2
    evs = await _events_for(pool, "retry-ok")
    completed = [e for e in evs if e.type == "AgentInvocationCompleted"][0]
    assert completed.payload["outcome"] == "ok"
    assert completed.payload["attempt_count"] == 2
    assert completed.payload["acceptance_mode"] is False


async def test_default_env_two_transient_failures_raise(pool):
    tenant, _ = await create_tenant(pool, "retry-fail", "Retry Fail")
    prov = _TransientThenSuccessProvider(fail_times=2)
    brain = Brain(pool, resolver=_resolver(prov))
    with pytest.raises(ConnectionResetError):
        await brain.invoke(_envelope(tenant.tenant_id), "flaky")
    assert prov.calls == 2
    evs = await _events_for(pool, "retry-fail")
    completed = [e for e in evs if e.type == "AgentInvocationCompleted"][0]
    assert completed.payload["outcome"] == "error"
    assert completed.payload["attempt_count"] == 2


async def test_acceptance_mode_caps_retry_at_one_attempt(pool, monkeypatch):
    monkeypatch.setenv("BROKAID_ACCEPTANCE_MODE", "true")
    tenant, _ = await create_tenant(pool, "retry-acc", "Retry Acc")
    prov = _TransientThenSuccessProvider(fail_times=1)
    brain = Brain(pool, resolver=_resolver(prov))
    with pytest.raises(ConnectionResetError):
        await brain.invoke(_envelope(tenant.tenant_id), "flaky")
    assert prov.calls == 1
    evs = await _events_for(pool, "retry-acc")
    completed = [e for e in evs if e.type == "AgentInvocationCompleted"][0]
    assert completed.payload["outcome"] == "error"
    assert completed.payload["attempt_count"] == 1
    assert completed.payload["acceptance_mode"] is True


async def test_non_transient_failure_is_not_retried(pool):
    tenant, _ = await create_tenant(pool, "retry-nt", "Retry NT")
    prov = _AlwaysNonTransient()
    brain = Brain(pool, resolver=_resolver(prov))
    with pytest.raises(ValueError):
        await brain.invoke(_envelope(tenant.tenant_id), "fatal")
    evs = await _events_for(pool, "retry-nt")
    completed = [e for e in evs if e.type == "AgentInvocationCompleted"][0]
    assert completed.payload["attempt_count"] == 1



class _Fake5xxError(Exception):
    """Mimics a provider SDK exception with an attached HTTP 5xx status.
    _is_transient(...) inspects `status_code` — this covers the
    "insurer/LLM SDK returned 502 once" fault-injection path.
    """
    def __init__(self, status: int = 502):
        super().__init__(f"upstream {status}")
        self.status_code = status


class _FiveXXThenSuccess:
    agent_name = "flaky-5xx"

    def __init__(self, fail_times: int = 1, status: int = 502):
        self._left = fail_times
        self._status = status
        self.calls = 0

    async def run(self, envelope):
        self.calls += 1
        if self._left > 0:
            self._left -= 1
            raise _Fake5xxError(self._status)
        from app.kernel.contracts.models import OutputContract, TokenCount
        return OutputContract(
            envelope_id=envelope.envelope_id,
            class_key=envelope.class_key,
            decision="propose",
            payload={"ok_after_5xx": True},
            citations=[],
            notes="ok",
            tokens_used=TokenCount(input=5, output=5),
            wall_ms=1,
            provider_meta={"mocked": True, "model_id": "fake"},
        )


async def test_default_env_5xx_transient_retried_and_succeeds(pool):
    """Fault-injection: single 502 on first attempt.
    Default env → retry succeeds, attempt_count=2 on the ledger.
    """
    tenant, _ = await create_tenant(pool, "retry-5xx-ok", "Retry 5xx OK")
    prov = _FiveXXThenSuccess(fail_times=1, status=502)
    brain = Brain(pool, resolver=_resolver(prov))
    out = await brain.invoke(_envelope(tenant.tenant_id), "flaky-5xx")
    assert out.payload == {"ok_after_5xx": True}
    assert prov.calls == 2
    evs = await _events_for(pool, "retry-5xx-ok")
    completed = [e for e in evs if e.type == "AgentInvocationCompleted"][0]
    assert completed.payload["outcome"] == "ok"
    assert completed.payload["attempt_count"] == 2
    assert completed.payload["acceptance_mode"] is False


async def test_acceptance_mode_5xx_transient_not_retried(pool, monkeypatch):
    """Fault-injection: single 502 with BROKAID_ACCEPTANCE_MODE=true.
    Cap=1 → no retry, attempt_count=1 on the ledger, outcome=error.
    """
    monkeypatch.setenv("BROKAID_ACCEPTANCE_MODE", "true")
    tenant, _ = await create_tenant(pool, "retry-5xx-acc", "Retry 5xx Acc")
    prov = _FiveXXThenSuccess(fail_times=1, status=502)
    brain = Brain(pool, resolver=_resolver(prov))
    with pytest.raises(_Fake5xxError):
        await brain.invoke(_envelope(tenant.tenant_id), "flaky-5xx")
    assert prov.calls == 1
    evs = await _events_for(pool, "retry-5xx-acc")
    completed = [e for e in evs if e.type == "AgentInvocationCompleted"][0]
    assert completed.payload["outcome"] == "error"
    assert completed.payload["attempt_count"] == 1
    assert completed.payload["acceptance_mode"] is True


async def test_4xx_never_retried(pool):
    """A 4xx-style provider exception must NOT be treated as transient
    regardless of acceptance mode.
    """
    class _Fake4xx(Exception):
        def __init__(self):
            super().__init__("400 bad request")
            self.status_code = 400
    class _Prov:
        agent_name = "flaky-4xx"
        def __init__(self): self.calls = 0
        async def run(self, envelope):
            self.calls += 1
            raise _Fake4xx()
    tenant, _ = await create_tenant(pool, "retry-4xx", "Retry 4xx")
    prov = _Prov()
    brain = Brain(pool, resolver=_resolver(prov))
    with pytest.raises(_Fake4xx):
        await brain.invoke(_envelope(tenant.tenant_id), "flaky-4xx")
    assert prov.calls == 1  # ONE call, no retry
    evs = await _events_for(pool, "retry-4xx")
    completed = [e for e in evs if e.type == "AgentInvocationCompleted"][0]
    assert completed.payload["attempt_count"] == 1
