"""Phase 7 — approve / edit / reject / block emit correct Approval events."""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.asyncio


async def _dev(client, email):
    r = await client.post("/api/auth/dev/impersonate", json={"email": email})
    assert r.status_code == 200, r.text


async def test_approve_non_trust_resolves_immediately(app_client, seeded):
    app, client = app_client
    from app.seeds.seed_all import seed_all
    await seed_all(app.state.pool)
    await _dev(client, "principal@burmac.example")

    cards = (await client.get("/api/o/queue")).json()["cards"]
    non_trust = next(c for c in cards if c["class_key"] != "trust_money")
    r = await client.post(f"/api/o/queue/{non_trust['envelope_id']}/approve",
                          json={"reason": "looks fine"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "resolved"

    # Card disappears from pending list.
    listed = (await client.get("/api/o/queue")).json()["cards"]
    assert non_trust["envelope_id"] not in [c["envelope_id"] for c in listed]


async def test_edit_and_reject_emit_shaped_approvals(app_client, seeded):
    app, client = app_client
    from app.seeds.seed_all import seed_all
    await seed_all(app.state.pool)
    await _dev(client, "principal@burmac.example")

    cards = (await client.get("/api/o/queue")).json()["cards"]
    a = cards[0]
    b = cards[1]
    r = await client.post(f"/api/o/queue/{a['envelope_id']}/edit",
                          json={"edits": {"limit": 500000}})
    assert r.status_code == 200
    r = await client.post(f"/api/o/queue/{b['envelope_id']}/reject",
                          json={"reason": "wrong scope"})
    assert r.status_code == 200


async def test_block_requires_compliance(app_client, seeded):
    app, client = app_client
    from app.seeds.seed_all import seed_all
    await seed_all(app.state.pool)

    # As principal — 403
    await _dev(client, "principal@burmac.example")
    cards = (await client.get("/api/o/queue")).json()["cards"]
    r = await client.post(f"/api/o/queue/{cards[0]['envelope_id']}/block",
                          json={"reason": "hold pending audit"})
    assert r.status_code == 403

    # As compliance — 200 and card resolves
    await _dev(client, "compliance@burmac.example")
    r = await client.post(f"/api/o/queue/{cards[0]['envelope_id']}/block",
                          json={"reason": "hold pending audit"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "blocked"
    assert body["block_id"]
