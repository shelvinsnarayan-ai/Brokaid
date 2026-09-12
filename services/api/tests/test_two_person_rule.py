"""Phase 4 — Two-person rule for trust_money HITL resolution."""
from __future__ import annotations

import uuid

import pytest

from app.kernel.trail import read_events
from app.kernel.two_person import record_approval

pytestmark = pytest.mark.asyncio


async def test_single_accounts_approval_pending_no_bypass(seeded, pool):
    tenant_id = seeded["tenant_id"]
    accounts_a = next(o for o in seeded["operators"]
                      if o["email"] == "accounts-a@burmac.example")
    envelope_id = str(uuid.uuid4())

    result = await record_approval(
        pool, tenant_id=tenant_id, envelope_id=envelope_id,
        class_key="trust_money",
        user_id=accounts_a["user_id"], role="accounts",
    )
    assert result == "pending"

    _, events = await read_events(pool, "burmac")
    pending = [e for e in events if e.type == "TwoPersonRulePending"
               and e.payload["envelope_id"] == envelope_id]
    satisfied = [e for e in events if e.type == "TwoPersonRuleSatisfied"
                 and e.payload["envelope_id"] == envelope_id]
    assert len(pending) == 1
    assert satisfied == []


async def test_two_distinct_accounts_satisfies(seeded, pool):
    tenant_id = seeded["tenant_id"]
    a = next(o for o in seeded["operators"]
             if o["email"] == "accounts-a@burmac.example")
    b = next(o for o in seeded["operators"]
             if o["email"] == "accounts-b@burmac.example")
    envelope_id = str(uuid.uuid4())

    r1 = await record_approval(
        pool, tenant_id=tenant_id, envelope_id=envelope_id,
        class_key="trust_money", user_id=a["user_id"], role="accounts",
    )
    assert r1 == "pending"
    r2 = await record_approval(
        pool, tenant_id=tenant_id, envelope_id=envelope_id,
        class_key="trust_money", user_id=b["user_id"], role="accounts",
    )
    assert r2 == "satisfied"

    _, events = await read_events(pool, "burmac")
    satisfied = [e for e in events if e.type == "TwoPersonRuleSatisfied"
                 and e.payload["envelope_id"] == envelope_id]
    assert len(satisfied) == 1
    signers = set(satisfied[0].payload["signers"])
    assert signers == {a["user_id"], b["user_id"]}


async def test_same_accounts_user_twice_still_pending(seeded, pool):
    tenant_id = seeded["tenant_id"]
    a = next(o for o in seeded["operators"]
             if o["email"] == "accounts-a@burmac.example")
    envelope_id = str(uuid.uuid4())

    await record_approval(
        pool, tenant_id=tenant_id, envelope_id=envelope_id,
        class_key="trust_money", user_id=a["user_id"], role="accounts",
    )
    result = await record_approval(
        pool, tenant_id=tenant_id, envelope_id=envelope_id,
        class_key="trust_money", user_id=a["user_id"], role="accounts",
    )
    assert result == "pending"

    _, events = await read_events(pool, "burmac")
    pending_dup = [e for e in events if e.type == "TwoPersonRulePending"
                   and e.payload["envelope_id"] == envelope_id
                   and e.payload["duplicate_signer"] is True]
    satisfied = [e for e in events if e.type == "TwoPersonRuleSatisfied"
                 and e.payload["envelope_id"] == envelope_id]
    assert len(pending_dup) == 1
    assert satisfied == []


async def test_principal_alone_no_bypass(seeded, pool):
    tenant_id = seeded["tenant_id"]
    principal = next(o for o in seeded["operators"]
                     if o["role"] == "principal")
    envelope_id = str(uuid.uuid4())

    result = await record_approval(
        pool, tenant_id=tenant_id, envelope_id=envelope_id,
        class_key="trust_money",
        user_id=principal["user_id"], role="principal",
    )
    # Principal is not role=accounts; the accounts-signer count is 0.
    assert result == "pending"

    _, events = await read_events(pool, "burmac")
    satisfied = [e for e in events if e.type == "TwoPersonRuleSatisfied"
                 and e.payload["envelope_id"] == envelope_id]
    assert satisfied == []


async def test_principal_plus_one_accounts_still_pending(seeded, pool):
    """Even a principal + one accounts user is only ONE valid signer."""
    tenant_id = seeded["tenant_id"]
    principal = next(o for o in seeded["operators"] if o["role"] == "principal")
    a = next(o for o in seeded["operators"]
             if o["email"] == "accounts-a@burmac.example")
    envelope_id = str(uuid.uuid4())

    await record_approval(
        pool, tenant_id=tenant_id, envelope_id=envelope_id,
        class_key="trust_money",
        user_id=principal["user_id"], role="principal",
    )
    result = await record_approval(
        pool, tenant_id=tenant_id, envelope_id=envelope_id,
        class_key="trust_money", user_id=a["user_id"], role="accounts",
    )
    assert result == "pending"
