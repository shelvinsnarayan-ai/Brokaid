"""Phase 5 — jurisdiction packs + preview mechanic."""
from __future__ import annotations

import pytest

from app.seeds.seed_all import seed_all

pytestmark = pytest.mark.asyncio


async def test_jurisdictions_list_returns_au_and_sg(app_client):
    _, client = app_client
    r = await client.get("/api/jurisdictions")
    assert r.status_code == 200
    codes = sorted(p["code"] for p in r.json()["jurisdictions"])
    assert codes == ["AU", "SG"]


async def test_au_pack_shape(app_client):
    _, client = app_client
    r = await client.get("/api/jurisdictions/AU")
    b = r.json()
    assert b["currency_code"] == "AUD"
    assert b["rail_label"] == "PayTo"
    assert b["locale"] == "en-AU"
    assert b["regulator"] == "ASIC"


async def test_sg_pack_shape(app_client):
    _, client = app_client
    r = await client.get("/api/jurisdictions/SG")
    b = r.json()
    assert b["currency_code"] == "SGD"
    assert b["rail_label"] == "PayNow"
    assert b["locale"] == "en-SG"
    assert b["regulator"] == "MAS"


async def test_every_disclosure_string_is_marked_placeholder(app_client):
    _, client = app_client
    for code in ("AU", "SG"):
        r = await client.get(f"/api/jurisdictions/{code}")
        for name, text in r.json()["disclosure_strings"].items():
            assert "<FICTIONAL_PLACEHOLDER>" in text, f"{code}.{name} missing marker"


async def test_unknown_jurisdiction_404(app_client):
    _, client = app_client
    r = await client.get("/api/jurisdictions/US")
    assert r.status_code == 404


async def test_client_preview_flips_currency_and_flags_preview_mode(app_client):
    app, client = app_client
    pool = app.state.pool
    await seed_all(pool)

    r = await client.post(
        "/api/auth/dev/impersonate",
        json={"email": "owner@fictionalclient.example"},
    )
    assert r.status_code == 200

    # Baseline — client is AU.
    r = await client.get("/api/c/policies")
    assert r.json()["jurisdiction"]["currency_code"] == "AUD"
    assert r.json()["preview_mode"] is False

    # Flip to SG.
    r = await client.post("/api/c/preferences", json={"jurisdiction": "SG"})
    assert r.status_code == 200
    r = await client.get("/api/c/policies")
    b = r.json()
    assert b["jurisdiction"]["currency_code"] == "SGD"
    assert b["jurisdiction"]["rail_label"] == "PayNow"
    assert b["preview_mode"] is True
    assert b["actual_jurisdiction"] == "AU"
    # Policies themselves show the pack's currency, not the ledger currency.
    for p in b["policies"]:
        assert p["premium"]["currency"] == "SGD"

    # Clear preview → back to AU.
    r = await client.post("/api/c/preferences", json={"jurisdiction": None})
    assert r.status_code == 200
    r = await client.get("/api/c/policies")
    assert r.json()["jurisdiction"]["currency_code"] == "AUD"
    assert r.json()["preview_mode"] is False


async def test_preview_endpoint_refused_when_dev_mode_off(pool, monkeypatch):
    """Setting a preview jurisdiction outside AUTH_DEV_MODE is 403."""
    import httpx

    monkeypatch.setenv("AUTH_DEV_MODE", "false")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)

    await seed_all(pool)

    from app.main import create_app
    app = create_app(pool_override=pool, seed_dev_users=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            # Client logs in via magic-link so we can hit /api/c/preferences.
            # Dev impersonation isn't mounted when AUTH_DEV_MODE=false.
            # We instead directly create a client session.
            from app.auth import sessions as sess_mod
            from app.auth.sessions import COOKIE_NAME
            # Seed already loaded — grab the client user id + tenant.
            async with pool.acquire() as c:
                u = await c.fetchrow(
                    "SELECT user_id::text FROM users "
                    "WHERE email = 'owner@fictionalclient.example'"
                )
                link = await c.fetchrow(
                    "SELECT tenant_id::text, client_id::text, role "
                    "  FROM user_client_links WHERE user_id = $1::uuid LIMIT 1",
                    u["user_id"],
                )
            s = await sess_mod.create(
                pool, user_id=u["user_id"], tenant_id=link["tenant_id"],
                client_id=link["client_id"], active_role=link["role"],
                mfa_verified=True,
            )
            client.cookies.set(COOKIE_NAME, s.session_id)
            r = await client.post(
                "/api/c/preferences", json={"jurisdiction": "SG"}
            )
    assert r.status_code == 403
    assert r.json()["detail"] == "preview_only_in_dev_mode"
