"""Phase 13 — regressions: no `mocked=false` on insurer events, and
the claim flow never touches the two-person-rule (trust_money) path.
"""
from __future__ import annotations

import pytest


pytestmark = pytest.mark.asyncio


async def test_claim_mocked_true_on_insurer_events(app_client, scene_seeded, pool):
    _, client = app_client
    owner = next(
        c for c in scene_seeded["clients"] if c["role"] == "client_owner"
    )
    r = await client.post(
        "/api/auth/dev/impersonate", json={"email": owner["email"]},
    )
    assert r.status_code == 200
    r = await client.post(
        "/api/c/claims",
        json={"description": "Storm damage to roof last night. Multiple leaks."},
    )
    assert r.status_code == 200

    tenant_id = scene_seeded["tenant_id"]
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            rows = await c.fetch(
                "SELECT type, mocked FROM events "
                " WHERE tenant_id=$1::uuid "
                "   AND type IN ('InsurerLodgementRequested', 'InsurerLodgementAck')",
                tenant_id,
            )
    assert len(rows) >= 2
    for r in rows:
        assert r["mocked"] is True, f"{r['type']} must carry mocked=true"


async def test_claim_two_person_rule_not_triggered(app_client, scene_seeded, pool):
    _, client = app_client
    owner = next(
        c for c in scene_seeded["clients"] if c["role"] == "client_owner"
    )
    r = await client.post(
        "/api/auth/dev/impersonate", json={"email": owner["email"]},
    )
    assert r.status_code == 200
    r = await client.post(
        "/api/c/claims",
        json={"description": "Van broken into overnight. Tools stolen from tray."},
    )
    assert r.status_code == 200

    tenant_id = scene_seeded["tenant_id"]
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            offending = await c.fetch(
                "SELECT type FROM events "
                " WHERE tenant_id=$1::uuid "
                "   AND type IN ('TwoPersonRuleSatisfied','TwoPersonRuleRecorded',"
                "                'TrustMoneyRelease','TrustMoneyReleasePending')",
                tenant_id,
            )
    assert len(offending) == 0, (
        f"claim flow must not touch trust_money path; saw {[r['type'] for r in offending]}"
    )
