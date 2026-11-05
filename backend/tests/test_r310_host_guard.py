"""R3.10 public host guard: dev mode needs a preview APP_BASE_URL and a preview served host; config problems never crash boot;
demo dev-password users are disabled outside dev mode; the exposure report lists impersonation sessions."""
from __future__ import annotations

import uuid

import pytest

from app.auth import passwords
from app.db.tenant_safe import wrap
from app.kernel import auth_flows as af, demo_credentials, devmode
from app.kernel.tenant_ctx import use_tenant
from app.seeds.seed_users import DEV_PASSWORD
from tests.test_r34_claim_threads import _staff_cookie
from tests.test_sec_audit import _operator, _tenant

PREVIEW = "https://x.preview.emergentagent.com"


def test_hosts():
    for h in ("localhost", "127.0.0.1:8001", "brokaid-demo.preview.emergentagent.com", "a.cluster-8.preview.emergentcf.cloud", "testserver"):
        assert devmode.is_preview_host(h), h
    for h in ("www.brokaid.com", "brokaid.com", "preview.emergentagent.com.evil.com", "", None, "emergentagent.com"):
        assert not devmode.is_preview_host(h), h


def test_public_base_url_turns_dev_mode_off(monkeypatch):
    from app.kernel.release_check import exposed_dev_routes
    from app.main import create_app
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("AUTH_DEV_MODE", "true")
    monkeypatch.setenv("APP_BASE_URL", "https://www.brokaid.com")
    monkeypatch.setenv("CORS_ORIGINS", "*")
    assert not devmode.dev_mode() and devmode.effective_mode() == "production"
    w = devmode.config_warnings()
    assert any("not a preview host" in x for x in w) and any("CORS_ORIGINS='*' is ignored" in x for x in w)
    assert exposed_dev_routes(create_app(seed_dev_users=False)) == []  # boots (CORS '*' doesn't raise), nothing mounted


@pytest.mark.asyncio
async def test_request_host_backstop_and_build_info(app_client, mongo_db, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("AUTH_DEV_MODE", "true")
    monkeypatch.setenv("APP_BASE_URL", PREVIEW)
    _, http = app_client
    demo = await _operator(mongo_db, await _tenant(mongo_db, f"ci-hg-{uuid.uuid4().hex[:4]}"))
    assert (await http.post("/api/auth/dev/impersonate", json={"email": demo}, headers={"host": "x.preview.emergentagent.com"})).status_code == 200
    assert (await http.post("/api/auth/dev/impersonate", json={"email": demo})).status_code == 200  # testserver = preview (in-process)
    for hdr in ({"host": "www.brokaid.com"}, {"host": "x.preview.emergentagent.com", "x-forwarded-host": "www.brokaid.com"}):
        assert (await http.get("/api/auth/dev/personas", headers=hdr)).status_code == 404, hdr
        assert (await http.post("/api/auth/dev/impersonate", headers=hdr, json={"email": demo})).status_code == 404, hdr
        assert (await http.post("/api/auth/dev/demo/kai-reset", headers=hdr)).status_code == 404, hdr
        assert (await http.post("/api/auth/operator/login", headers=hdr, json={"email": demo, "password": "x"})).status_code == 404, hdr
        assert (await http.post("/api/auth/client/request", headers=hdr, json={"email": demo})).status_code == 404, hdr
    bi = (await http.get("/api/build-info", headers={"host": "x.preview.emergentagent.com", "x-forwarded-host": "www.brokaid.com"})).json()
    assert bi["served_host"] == "www.brokaid.com" and bi["demo_on_public_host"] is True and bi["effective_mode"] == "preview"
    bi = (await http.get("/api/build-info", headers={"host": "x.preview.emergentagent.com"})).json()
    assert bi["dev_mode_active"] and not bi["demo_on_public_host"] and bi["config_warnings"] == []
    monkeypatch.setenv("APP_BASE_URL", "https://www.brokaid.com")
    bi = (await http.get("/api/build-info", headers={"host": "www.brokaid.com"})).json()
    assert bi["effective_mode"] == "production" and bi["dev_mode_active"] is False and bi["config_warnings"]
    assert (await http.post("/api/auth/dev/impersonate", json={"email": demo}, headers={"host": "x.preview.emergentagent.com"})).status_code == 404
    assert (await http.get("/api/ready")).json()["config_warnings"]


@pytest.mark.asyncio
async def test_demo_credentials_disabled_off_preview(app_client, mongo_db, monkeypatch):
    _, http = app_client
    h = passwords.hash_password(DEV_PASSWORD)
    demo_t, real_t = await _tenant(mongo_db, f"ci-dc-{uuid.uuid4().hex[:4]}"), await _tenant(mongo_db, f"real-{uuid.uuid4().hex[:4]}", kind="brokerage")
    demo_email, shared = f"d-{uuid.uuid4().hex[:6]}@dc.example", f"s-{uuid.uuid4().hex[:6]}@dc.example"
    for tid, email in ((demo_t, demo_email), (demo_t, shared), (real_t, shared)):
        async with use_tenant(tid):
            await wrap(mongo_db).users.insert_one({"user_id": str(uuid.uuid4()), "email": email, "kind": "operator", "password_hash": h})
    for email in (demo_email, shared):
        await af.set_identity(mongo_db, email, {"password_hash": h, "password_set_at": af.now(), "mfa_secret": "JBSWY3DPEHPK3PXP",
                                                "recovery_codes": [{"hash": "x", "used_at": None}], "mfa_enrolled_at": af.now()})
    # preview + dev mode: untouched
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("AUTH_DEV_MODE", "true")
    monkeypatch.setenv("APP_BASE_URL", PREVIEW)
    assert (await demo_credentials.disable_all(mongo_db)) == {"skipped": "dev_mode"}
    # public host, dev mode refused → disabled + MFA revoked, real user's email untouched, ledgered once
    monkeypatch.setenv("APP_BASE_URL", "https://www.brokaid.com")
    out = await demo_credentials.disable_all(mongo_db)
    assert out["tenants"][demo_t] == {"disabled": [demo_email], "mfa_revoked": [demo_email]}
    ident = await af.identity(mongo_db, demo_email)
    assert ident["password_disabled"] and not ident["mfa_secret"] and ident["recovery_codes"] == []
    assert (await af.identity(mongo_db, shared))["mfa_secret"] == "JBSWY3DPEHPK3PXP"
    async with use_tenant(demo_t):
        assert await wrap(mongo_db).events.count_documents({"type": "DemoCredentialsDisabled"}) == 1
    assert (await demo_credentials.disable_all(mongo_db))["tenants"] == {}  # idempotent
    # request time: dev password refused outside dev mode (generic 401), even for a not-yet-disabled identity
    r = await http.post("/api/auth/login", json={"email": shared, "password": DEV_PASSWORD})
    assert r.status_code == 401
    r = await http.post("/api/auth/login", json={"email": demo_email, "password": DEV_PASSWORD})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_exposure_report_lists_impersonation(app_client, mongo_db, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("AUTH_DEV_MODE", "true")
    monkeypatch.setenv("APP_BASE_URL", PREVIEW)
    _, http = app_client
    tid = await _tenant(mongo_db, f"ci-ex-{uuid.uuid4().hex[:4]}")
    demo = await _operator(mongo_db, tid)
    assert (await http.post("/api/auth/dev/impersonate", json={"email": demo}, headers={"x-forwarded-for": "203.0.113.9"})).status_code == 200
    op = await _staff_cookie(mongo_db, tid, str(uuid.uuid4()), "operator")
    ae = await _staff_cookie(mongo_db, tid, str(uuid.uuid4()), "ae")
    assert (await http.get("/api/o/exposure-report", headers=ae)).status_code == 403
    rep = (await http.get("/api/o/exposure-report", headers=op, params={"since": "2026-01-01T00:00:00Z"})).json()
    row = next(s for s in rep["sessions"] if s["persona"] == demo)
    assert row["via"] == "dev_impersonate" and row["ip"] == "203.0.113.9" and row["served_host"] == "testserver"
