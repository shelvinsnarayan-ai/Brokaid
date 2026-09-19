"""Phase 13 — coverage-language guard visibly catches a bad operator
status update draft on a claim.

The guard-visible demo beat: an operator drafts
"You are covered for this event." on a claim's status update panel.
`try_release()` blocks at the deterministic coverage guard. The
response includes the offending span so the UI can highlight it.
"""
from __future__ import annotations

import pytest


pytestmark = pytest.mark.asyncio


DEV_PASSWORD = "dev-password-not-for-production"


async def _operator_login_verified(client, email: str, pool):
    r = await client.post(
        "/api/auth/operator/login",
        json={"email": email, "password": DEV_PASSWORD},
    )
    assert r.status_code == 200, r.text
    # Enrol + verify TOTP for MFA-gated routes.
    r = await client.post("/api/auth/operator/mfa/enrol")
    assert r.status_code == 200
    from app.auth import totp as totp_mod
    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT mfa_secret FROM users WHERE email=$1", email,
        )
    code = totp_mod.current_code(row["mfa_secret"])
    r = await client.post("/api/auth/operator/mfa/verify", json={"code": code})
    assert r.status_code == 200


async def _create_claim(client, email: str) -> str:
    """Impersonate the client-owner, submit a claim, return claim_id.
    Then log out so the caller can log in as an operator.
    """
    r = await client.post("/api/auth/dev/impersonate", json={"email": email})
    assert r.status_code == 200
    r = await client.post(
        "/api/c/claims",
        json={
            "incident_date": "2026-09-01",
            "description": "Small fire in the kitchen, no injuries. Damage to appliances only.",
        },
    )
    assert r.status_code == 200, r.text
    claim_id = r.json()["claim_id"]
    client.cookies.clear()
    return claim_id


async def test_coverage_guard_visible_on_status_update(
    app_client, scene_seeded, pool,
):
    app, client = app_client
    # 1. Client submits a claim so there's something to update.
    client_owner = next(
        c for c in scene_seeded["clients"] if c["role"] == "client_owner"
    )
    claim_id = await _create_claim(client, client_owner["email"])

    # 2. Operator logs in with MFA.
    principal = next(
        o for o in scene_seeded["operators"] if o["role"] == "principal"
    )
    await _operator_login_verified(client, principal["email"], pool)

    # 3. Operator drafts a bad status update — direct coverage assertion.
    bad_draft = (
        "You are covered for this event. We'll pay the full amount today."
    )
    r = await client.post(
        f"/api/o/claims/{claim_id}/status-update",
        json={"draft": bad_draft},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["released"] is False
    assert body["verdict"] == "coverage_guard_block"
    assert body["guard_visible"] is True
    assert body["blocked_spans"], "must return offending spans"
    # At least one span points at the "covered" phrase.
    joined = " ".join(s["text"] for s in body["blocked_spans"]).lower()
    assert "covered" in joined

    # 4. No ClientMessageReleased event exists for this claim.
    tenant_id = scene_seeded["tenant_id"]
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            released = await c.fetch(
                "SELECT event_id FROM events "
                " WHERE tenant_id=$1::uuid AND type='ClientMessageReleased'",
                tenant_id,
            )
    assert len(released) == 0, "guard must prevent ClientMessageReleased"
