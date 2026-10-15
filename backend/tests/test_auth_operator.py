"""Phase 4 — operator login + TOTP enrolment/verify."""
from __future__ import annotations

import pytest

from app.auth import totp as totp_mod

pytestmark = pytest.mark.asyncio


DEV_PASSWORD = "dev-password-not-for-production"


async def test_login_wrong_password_returns_401(app_client, seeded):
    _, client = app_client
    r = await client.post(
        "/api/auth/operator/login",
        json={"email": "principal@harbourline.example", "password": "wrong"},
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid_credentials"
    assert "brokaid_session" not in r.cookies


async def test_login_unknown_user_returns_401(app_client, seeded):
    _, client = app_client
    r = await client.post(
        "/api/auth/operator/login",
        json={"email": "nobody@harbourline.example", "password": DEV_PASSWORD},
    )
    assert r.status_code == 401


async def test_login_success_sets_cookie_and_flags_mfa_required(app_client, seeded):
    _, client = app_client
    r = await client.post(
        "/api/auth/operator/login",
        json={"email": "principal@harbourline.example", "password": DEV_PASSWORD},
    )
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "mfa_required": True,
        "enrolment_required": True,
        "active_role": "principal",
    }
    assert "brokaid_session" in client.cookies


async def test_enrol_then_verify_upgrades_session(app_client, seeded, pool):
    _, client = app_client
    await client.post(
        "/api/auth/operator/login",
        json={"email": "principal@harbourline.example", "password": DEV_PASSWORD},
    )
    r = await client.post("/api/auth/operator/mfa/enrol")
    assert r.status_code == 200
    body = r.json()
    assert body["otpauth_uri"].startswith("otpauth://totp/")
    assert body["qr_svg"].startswith("<?xml") or body["qr_svg"].startswith("<svg")

    # Fetch the stored secret to compute the code.
    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT mfa_secret FROM users WHERE email = 'principal@harbourline.example'"
        )
    code = totp_mod.current_code(row["mfa_secret"])

    r = await client.post("/api/auth/operator/mfa/verify", json={"code": code})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


async def test_unverified_session_hits_operator_endpoint_returns_401(app_client, seeded):
    _, client = app_client
    # Login (pre-MFA).
    await client.post(
        "/api/auth/operator/login",
        json={"email": "compliance@harbourline.example", "password": DEV_PASSWORD},
    )
    # Attempt a compliance action without MFA verified.
    r = await client.post(
        "/api/o/compliance/block",
        json={"target_kind": "matter", "target_id": "abc", "reason": "test"},
    )
    # Not MFA-verified — session middleware refuses.
    # (require_operator_session used by protected endpoints; requires_role
    #  in compliance router goes via require_session but the guard also
    #  enforces mfa via the operator dependency chain. Here compliance
    #  uses requires_role which calls require_session; we accept 401 or 403.
    #  The concrete guarantee is: NOT 200.)
    assert r.status_code in (401, 403)


async def test_data_only_role_login_rejected(app_client, seeded):
    _, client = app_client
    r = await client.post(
        "/api/auth/operator/login",
        json={"email": "auditor@harbourline.example", "password": DEV_PASSWORD},
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "no_login_capable_role"
