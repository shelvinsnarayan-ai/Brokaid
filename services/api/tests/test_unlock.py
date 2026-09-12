"""Unlock protocol: 60 decisions with 96% agreement → unlocked, then E&O miss → relocked."""
from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.kernel.gate.unlock import apply_human_decision
from app.kernel.trail import append, create_tenant

pytestmark = pytest.mark.asyncio


async def _record_decision(
    pool, tenant_id: str, class_key: str, agreed: bool, eno: bool
):
    # emit a HumanDecision event, then run projection
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            ev = await append(
                pool, tenant_id, "HumanDecision",
                actor={"kind": "human", "id": "op", "name": "Operator"},
                payload={"class_key": class_key, "agreed": agreed, "eno_flag": eno},
                idempotency_key=f"hd:{class_key}:{uuid.uuid4()}",
                conn=conn,
            )
            return await apply_human_decision(
                conn, tenant_id=tenant_id, class_key=class_key,
                agreed_with_agent=agreed, eno_flag=eno, event_id=ev.event_id,
            )


async def test_unlock_and_relock(pool):
    tenant, _ = await create_tenant(pool, slug="unlock", display_name="Unlock Co")
    class_key = "endorsement_standard"

    # 58 agreements, 2 non-agreements (no eno) → 58/60 = 96.7%
    agree_count = 0
    for i in range(60):
        agreed = i not in (5, 25)   # 2 disagreements, no eno flag
        eno = False
        _, kind = await _record_decision(pool, tenant.tenant_id, class_key, agreed, eno)
        if agreed:
            agree_count += 1

    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant.tenant_id)
            row = await c.fetchrow(
                "SELECT decisions_seen, agreements, eno_misses, unlocked "
                "FROM proj_class_unlock WHERE tenant_id=$1::uuid AND class_key=$2",
                tenant.tenant_id, class_key,
            )
    assert row["decisions_seen"] == 60
    assert row["agreements"] == 58
    assert row["eno_misses"] == 0
    assert row["unlocked"] is True, row

    # One E&O miss → relock
    _, kind = await _record_decision(pool, tenant.tenant_id, class_key, agreed=False, eno=True)
    assert kind == "ClassRelocked"

    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant.tenant_id)
            row = await c.fetchrow(
                "SELECT decisions_seen, agreements, eno_misses, unlocked, last_relock_reason "
                "FROM proj_class_unlock WHERE tenant_id=$1::uuid AND class_key=$2",
                tenant.tenant_id, class_key,
            )
    assert row["decisions_seen"] == 61
    assert row["eno_misses"] == 1
    assert row["unlocked"] is False
    assert row["last_relock_reason"] == "eno_flag miss"
