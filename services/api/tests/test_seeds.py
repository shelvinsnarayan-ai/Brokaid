"""Phase 5 — seed idempotency + Program View endpoints."""
from __future__ import annotations

import pytest

from app.kernel.trail import read_events
from app.seeds.seed_all import seed_all

pytestmark = pytest.mark.asyncio


async def test_seed_all_is_idempotent(pool):
    r1 = await seed_all(pool)
    r2 = await seed_all(pool)
    assert r1["tenant_id"] == r2["tenant_id"]
    assert r1["client_id"] == r2["client_id"]
    assert r1["policy_ids"] == r2["policy_ids"]

    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", r1["tenant_id"]
            )
            n_clients = await c.fetchval("SELECT COUNT(*) FROM clients")
            n_policies = await c.fetchval("SELECT COUNT(*) FROM policies")
        n_brokerages = await c.fetchval("SELECT COUNT(*) FROM brokerages")
    assert n_clients == 1
    assert n_policies == 2
    assert n_brokerages == 1

    _, events = await read_events(pool, "burmac")
    # Each seed emits a single ledger event of its type after two runs.
    for etype in ("LicenseeCreated", "BrokerageCreated",
                  "ClientEntityCreated"):
        assert len([e for e in events if e.type == etype]) == 1, etype
    assert len([e for e in events if e.type == "PolicyCreated"]) == 2


async def test_operator_policies_returns_both_with_chips(app_client):
    app, client = app_client
    pool = app.state.pool
    await seed_all(pool)

    # Impersonate an operator with MFA-verified session.
    r = await client.post(
        "/api/auth/dev/impersonate",
        json={"email": "principal@burmac.example"},
    )
    assert r.status_code == 200

    r = await client.get("/api/o/policies")
    assert r.status_code == 200
    body = r.json()
    statuses = sorted(p["status"] for p in body["policies"])
    assert statuses == ["managed", "tracked"]
    for p in body["policies"]:
        assert p["mocked_insurer"] is True
        assert p["premium"]["currency"] == "AUD"


async def test_client_policies_scoped_to_linked_client(app_client):
    app, client = app_client
    pool = app.state.pool
    await seed_all(pool)

    r = await client.post(
        "/api/auth/dev/impersonate",
        json={"email": "owner@fictionalclient.example"},
    )
    assert r.status_code == 200

    r = await client.get("/api/c/policies")
    assert r.status_code == 200
    body = r.json()
    assert len(body["policies"]) == 2
    assert body["actual_jurisdiction"] == "AU"
    assert body["jurisdiction"]["currency_code"] == "AUD"
    assert body["preview_mode"] is False
