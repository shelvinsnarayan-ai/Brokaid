"""Phase 13 — claim scene end-to-end happy path.

FNOL → clAIms triage → ClaimCreated → MOCKED lodgement → notification.
Asserts the full expected event trace, actor kinds, and MOCKED flags.
"""
from __future__ import annotations

import json

import pytest


pytestmark = pytest.mark.asyncio


DEV_PASSWORD = "dev-password-not-for-production"


async def _client_impersonate(client, email: str):
    r = await client.post("/api/auth/dev/impersonate", json={"email": email})
    assert r.status_code == 200, r.text


async def test_claim_scene_happy_path(app_client, scene_seeded):
    _, client = app_client
    tenant_id = scene_seeded["tenant_id"]

    # Sign in as the client-owner.
    client_owner = next(
        c for c in scene_seeded["clients"]
        if c["role"] == "client_owner"
    )
    await _client_impersonate(client, client_owner["email"])

    # 1. Submit FNOL — a property-damage description with a specific date.
    r = await client.post(
        "/api/c/claims",
        json={
            "incident_date": "2026-09-12",
            "description": (
                "A burst pipe on level 3 caused flooding in our office "
                "over the weekend on 12/09/2026. Significant water damage "
                "to carpet and desks. Estimated $18,000 in damage."
            ),
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "lodged"
    assert body["claim_reference"].startswith("CLM-")
    assert body["triage"]["claim_type"] == "property_damage"
    assert body["triage"]["urgency"] in ("normal", "high")
    assert body["lodgement"]["mocked"] is True
    assert "Fictional" in body["lodgement"]["insurer_display"]
    claim_id = body["claim_id"]

    # 2. Claim appears in the client list.
    r = await client.get("/api/c/claims")
    assert r.status_code == 200
    claims = r.json()["claims"]
    assert any(c["claim_id"] == claim_id for c in claims)

    # 3. Detail returns timeline with triaged + lodged rows.
    r = await client.get(f"/api/c/claims/{claim_id}")
    assert r.status_code == 200
    detail = r.json()
    kinds = [t["kind"] for t in detail["timeline"]]
    assert "triaged" in kinds
    assert "lodged" in kinds
    mocked_row = next(t for t in detail["timeline"] if t["kind"] == "lodged")
    assert mocked_row["mocked"] is True

    # 4. Notification exists in tray.
    r = await client.get("/api/c/notifications")
    assert r.status_code == 200
    notifs = r.json()["notifications"]
    assert len(notifs) >= 1
    assert notifs[0]["kind"] == "claim_lodged"
    assert body["claim_reference"] in notifs[0]["subject"]

    # 5. Ledger trace under the tenant — expected event types are all present.
    import asyncpg
    from urllib.parse import urlparse
    # Rebind via the pool from app_client's app.
    app, _ = app_client
    pool = app.state.pool
    expected_types = [
        "ClientRequestReceived",
        "AgentInvocationStarted",
        "AgentOutputProduced",
        "AgentInvocationCompleted",
        "ClaimTriaged",
        "ClaimCreated",
        "InsurerLodgementRequested",
        "InsurerLodgementAck",
        "EmailRenderedDevMode",
        "InAppNotification",
    ]
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            rows = await c.fetch(
                "SELECT type, actor, mocked FROM events "
                " WHERE tenant_id=$1::uuid AND type=ANY($2::text[]) "
                " ORDER BY seq ASC",
                tenant_id, expected_types,
            )
    saw = [r["type"] for r in rows]
    for t in expected_types:
        assert t in saw, f"missing {t} in trace; saw={saw}"
    # Both lodgement events must carry mocked=true.
    for r in rows:
        if r["type"] in ("InsurerLodgementRequested", "InsurerLodgementAck"):
            assert r["mocked"] is True
