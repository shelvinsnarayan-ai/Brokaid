"""Phase 4 — RBAC enforcement + AccessDenied events."""
from __future__ import annotations

import pytest

from app.kernel.trail import read_events

pytestmark = pytest.mark.asyncio

DEV_PASSWORD = "dev-password-not-for-production"


async def _login_and_impersonate(client, email: str):
    """Dev impersonation — sets an MFA-verified session cookie."""
    r = await client.post("/api/auth/dev/impersonate", json={"email": email})
    assert r.status_code == 200, r.text
    return r.json()


async def test_data_only_auditor_cannot_login(app_client, seeded):
    _, client = app_client
    r = await client.post(
        "/api/auth/operator/login",
        json={"email": "auditor@harbourline.example", "password": DEV_PASSWORD},
    )
    assert r.status_code == 403


async def test_wrong_role_hits_compliance_endpoint_returns_403_and_emits_event(
    app_client, seeded
):
    app, client = app_client
    # Impersonate a non-compliance user (principal).
    await _login_and_impersonate(client, "principal@harbourline.example")
    r = await client.post(
        "/api/o/compliance/block",
        json={"target_kind": "matter", "target_id": "m1", "reason": "x"},
    )
    assert r.status_code == 403

    _, events = await read_events(app.state.pool, "burmac")
    deny_events = [e for e in events if e.type == "AccessDenied"]
    assert len(deny_events) == 1
    assert deny_events[0].payload["required"] == ["compliance"]
    assert deny_events[0].payload["actual_role"] == "principal"


async def test_correct_role_passes(app_client, seeded):
    _, client = app_client
    await _login_and_impersonate(client, "compliance@harbourline.example")
    r = await client.post(
        "/api/o/compliance/block",
        json={"target_kind": "matter", "target_id": "m2", "reason": "kyc gap"},
    )
    assert r.status_code == 200
    assert "block_id" in r.json()
