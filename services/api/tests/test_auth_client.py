"""Phase 4 — client magic-link auth."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


CLIENT_EMAIL = "owner@fictionalclient.example"


async def test_request_dev_mode_returns_token(app_client, seeded):
    _, client = app_client
    r = await client.post(
        "/api/auth/client/request", json={"email": CLIENT_EMAIL}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["dev_mode"] is True
    assert "dev_token" in body
    assert len(body["dev_token"]) > 20


async def test_verify_token_creates_session(app_client, seeded):
    _, client = app_client
    r = await client.post(
        "/api/auth/client/request", json={"email": CLIENT_EMAIL}
    )
    token = r.json()["dev_token"]

    r = await client.get(f"/api/auth/client/verify?token={token}")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "brokaid_session" in client.cookies


async def test_verify_twice_second_attempt_400(app_client, seeded):
    _, client = app_client
    r = await client.post(
        "/api/auth/client/request", json={"email": CLIENT_EMAIL}
    )
    token = r.json()["dev_token"]

    r1 = await client.get(f"/api/auth/client/verify?token={token}")
    assert r1.status_code == 200

    r2 = await client.get(f"/api/auth/client/verify?token={token}")
    assert r2.status_code == 400
    assert r2.json()["detail"] == "already_used"


async def test_verify_expired_token(app_client, seeded, pool):
    _, client = app_client
    r = await client.post(
        "/api/auth/client/request", json={"email": CLIENT_EMAIL}
    )
    token = r.json()["dev_token"]

    # Force the token expired.
    async with pool.acquire() as c:
        await c.execute(
            "UPDATE magic_link_tokens SET expires_at = NOW() - INTERVAL '1 hour'"
        )

    r = await client.get(f"/api/auth/client/verify?token={token}")
    assert r.status_code == 400
    assert r.json()["detail"] == "expired"


async def test_verify_unknown_token(app_client, seeded):
    _, client = app_client
    r = await client.get("/api/auth/client/verify?token=not-a-real-token")
    assert r.status_code == 400
    assert r.json()["detail"] == "unknown"


async def test_no_dev_mode_no_resend_returns_503(pool, monkeypatch):
    """The two-guard fallback: neither dev-mode nor Resend → 503."""
    import httpx

    monkeypatch.setenv("AUTH_DEV_MODE", "false")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)

    # Seed users with dev-mode off for this test.
    from app.seeds.seed_users import seed
    await seed(pool)

    from app.main import create_app
    app = create_app(pool_override=pool, seed_dev_users=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            r = await client.post(
                "/api/auth/client/request", json={"email": CLIENT_EMAIL}
            )
    assert r.status_code == 503
    assert r.json()["detail"] == "email_provider_unavailable"
