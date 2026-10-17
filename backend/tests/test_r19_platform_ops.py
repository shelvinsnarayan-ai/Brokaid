"""R1.9 — platform admin + brokerage setup wizard, go-live checklist, suspension, monitoring, error capture + throttled owner
alert, backups/restore drill, security headers, CSRF origin check, rate limits, magic-byte uploads."""
from __future__ import annotations

import base64
import os
import uuid

import pytest
from fastapi import HTTPException

from app.db.tenant_safe import wrap
from app.kernel import backup, filetype, object_store, ops, platform
from app.kernel import claims as ck
from app.kernel.tenant_ctx import use_tenant
from app.middleware.security import _Window, csrf_ok

BLOBS: dict[str, bytes] = {}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    async def put(path, data, ct):
        BLOBS[path] = data
        return {"path": path}

    async def get(path):
        return BLOBS[path], "application/octet-stream"
    monkeypatch.setattr(object_store, "put_object", put)
    monkeypatch.setattr(object_store, "get_object", get)
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", base64.b64encode(b"k" * 32).decode())
    monkeypatch.setenv("PLATFORM_ADMIN_EMAILS", "padmin@platform.example")


def test_abn_checksum():
    assert platform.abn_valid("51 824 753 556") and platform.abn_valid("53004085616")
    assert not platform.abn_valid("51 824 753 557") and not platform.abn_valid("123")


async def _wizard(db, slug=None):
    sent = []

    async def send(**kw):
        sent.append(kw)
    body = {"slug": slug or f"bk-{uuid.uuid4().hex[:6]}", "legal_name": "Example Brokers Pty Ltd", "abn": "51 824 753 556",
            "afsl": "AR 001234567", "state": "VIC", "timezone": "Australia/Melbourne", "operator_email": "op@example-brokers.example",
            "operator_name": "Example Operator"}
    out = await platform.create(db, body=body, by="padmin", send=send)
    return out, sent


@pytest.mark.asyncio
async def test_wizard_creates_tenant_with_genesis_event_invite_and_checklist(mongo_db):
    out, sent = await _wizard(mongo_db)
    tid = out["tenant_id"]
    t = await mongo_db.tenants.find_one({"tenant_id": tid})
    assert t["status"] == "setup" and t["abn"] == "51824753556" and not t["test"]
    async with use_tenant(tid):
        types = [e["type"] async for e in wrap(mongo_db).events.find({}).sort("seq", 1)]
        st = await wrap(mongo_db).tenant_settings.find_one({})
        inv = await wrap(mongo_db).system("auth_tokens").find_one({"kind": "invite", "tenant_id": tid})
    assert types[0] == "TenantGenesis" and "TenantCreated" in types and st["quoting_mode"] == "manual_only"
    assert inv["role"] == "operator" and inv["email"] == "op@example-brokers.example"
    items = {i["key"]: i["done"] for i in await platform.checklist(mongo_db, tid)}
    assert items["quoting"] and items["holidays"]
    assert not any(items[k] for k in ("operator", "fsg", "gaw", "afca", "privacy", "payment", "iadviser", "email"))
    with pytest.raises(HTTPException) as e:
        await platform.set_status(mongo_db, tid, "live", "", "padmin")
    assert e.value.status_code == 409 and "FSG uploaded" in e.value.detail["missing"]
    with pytest.raises(HTTPException):
        await platform.create(mongo_db, body={"slug": t["slug"], "abn": "51824753556"}, by="padmin")  # slug taken


@pytest.mark.asyncio
async def test_invalid_abn_and_platform_admin_invite_refused_outside_bootstrap(mongo_db):
    from app.kernel import auth_flows as af
    with pytest.raises(HTTPException):
        await platform.create(mongo_db, body={"slug": "bad-abn", "legal_name": "X", "abn": "12345678901", "afsl": "1", "state": "NSW",
                                              "operator_email": "a@b.example"}, by="padmin")
    out, _ = await _wizard(mongo_db)
    with pytest.raises(HTTPException):  # a tenant Operator can't mint a platform_admin
        await af.create_invite(mongo_db, tenant_id=out["tenant_id"], email="padmin@platform.example", name="", role="platform_admin",
                               client_id=None, created_by="someone")


@pytest.mark.asyncio
async def test_suspended_tenant_cannot_sign_in_and_sessions_end(mongo_db):
    from app.kernel import auth_flows as af, identity
    out, _ = await _wizard(mongo_db)
    tid = out["tenant_id"]
    uid = str(uuid.uuid4())
    async with use_tenant(tid):
        await wrap(mongo_db).users.insert_one({"user_id": uid, "email": "op@example-brokers.example", "kind": "operator", "role": "operator"})
        await wrap(mongo_db).user_brokerage_roles.insert_one({"user_id": uid, "role": "operator", "active": True, "brokerage_id": tid})
    assert any(o["tenant_id"] == tid for o in await af.tenant_options(mongo_db, "op@example-brokers.example"))
    sid, _, _ = await af.issue_session(mongo_db, tid, uid, {"active_role": "operator", "staff": True, "client_id": None,
                                                            "brokerage_id": tid})
    assert await identity.read_session(mongo_db, sid) is not None
    await platform.set_status(mongo_db, tid, "suspended", "test", "padmin")
    assert not any(o["tenant_id"] == tid for o in await af.tenant_options(mongo_db, "op@example-brokers.example"))
    assert await identity.read_session(mongo_db, sid) is None


@pytest.mark.asyncio
async def test_platform_routes_refuse_tenant_roles(app_client):
    _, http = app_client
    assert (await http.get("/api/platform/tenants")).status_code in (401, 403)
    r = await http.get("/api/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    for h in ("content-security-policy", "strict-transport-security", "x-frame-options", "referrer-policy", "permissions-policy",
              "x-request-id"):
        assert h in r.headers
    assert "unsafe-inline" not in r.headers["content-security-policy"]
    rd = await http.get("/api/ready")
    assert set(rd.json()["checks"]) == {"mongo", "object_storage", "job_leader", "llm_key"}


@pytest.mark.asyncio
async def test_platform_admin_has_no_client_or_operator_surface(app_client, mongo_db):
    from app.kernel import auth_flows as af
    from app.auth.sessions import COOKIE_NAME
    _, http = app_client
    tid, uid = str(uuid.uuid4()), str(uuid.uuid4())
    sid, _, _ = await af.issue_session(mongo_db, tid, uid, {"active_role": "platform_admin", "staff": True, "client_id": None,
                                                            "brokerage_id": tid})
    ck = {"cookie": f"{COOKIE_NAME}={sid}"}
    for p in ("/api/c/me", "/api/c/things", "/api/c/claims"):
        assert (await http.get(p, headers=ck)).status_code in (401, 404), p
    assert (await http.get("/api/o/clients", headers=ck)).status_code in (401, 403)


def test_csrf_origin_check_and_rate_window(monkeypatch):
    from app.auth.sessions import COOKIE_NAME
    monkeypatch.setenv("APP_BASE_URL", "https://app.brokaid.example")
    ck_ = {COOKIE_NAME: "sid"}
    assert csrf_ok("POST", {"origin": "https://app.brokaid.example"}, ck_)
    assert not csrf_ok("POST", {"origin": "https://evil.example"}, ck_)
    assert not csrf_ok("POST", {"referer": "https://evil.example/x"}, ck_)
    assert not csrf_ok("POST", {"sec-fetch-site": "cross-site"}, ck_)
    assert csrf_ok("POST", {}, ck_)  # non-browser client: no ambient cookies to ride
    assert csrf_ok("GET", {"origin": "https://evil.example"}, ck_) and csrf_ok("POST", {"origin": "https://evil.example"}, {})
    w = _Window()
    assert all(w.hit("ip", 3, 100.0) for _ in range(3)) and not w.hit("ip", 3, 100.5) and w.hit("ip", 3, 161.0)


def test_magic_bytes():
    assert filetype.matches(b"%PDF-1.7 x", "application/pdf") and not filetype.matches(b"<html>", "application/pdf")
    assert filetype.matches(b"\xff\xd8\xff\xe0....", "image/jpeg") and not filetype.matches(b"%PDF-1.7", "image/jpeg")


@pytest.mark.asyncio
async def test_claim_upload_rejects_disguised_file(mongo_db):
    tid = str(uuid.uuid4())
    async with use_tenant(tid):
        await wrap(mongo_db).clients.insert_one({"client_id": "kai", "display_name": "Kai"})
    c = await ck.lodge(mongo_db, tid, client_id="kai", policy=None, body={"description": "Hail damage to the roof."}, by="u", role="client",
                       channel="in_app")
    with pytest.raises(ck.Bad, match="contents don't match"):
        await ck.add_file(mongo_db, tid, c, kind="photo", data=b"<script>alert(1)</script>", filename="x.jpg", content_type="image/jpeg",
                          keep_location=False, by="u", role="client")


@pytest.mark.asyncio
async def test_forced_error_is_captured_and_owner_alert_is_throttled(mongo_db, monkeypatch):
    monkeypatch.setenv("OWNER_ALERT_EMAILS", "owner@allowed.example")
    monkeypatch.setenv("EMAIL_ALLOWLIST", "*@allowed.example")
    from app.kernel import email_channel as ec
    monkeypatch.setattr(ec, "email_enabled", lambda: True)
    sent = []

    async def fake_send(**kw):
        sent.append(kw)
    monkeypatch.setattr(ec, "resend_send", fake_send)
    for _ in range(3):
        try:
            raise RuntimeError("Forced test error for kai@example.com 123456")
        except RuntimeError as e:
            row = await ops.capture_error(mongo_db, e, "/api/platform/ops/force-error", "req-1")
    assert row["count"] == 3 and "kai@example.com" not in row["sample_stack"] and "<email>" in row["sample_stack"]
    assert len(sent) == 1 and sent[0]["to"] == "owner@allowed.example"  # new-error alert once; repeats are throttled
    assert await ops.owner_alert(mongo_db, f"error:new:{row['fingerprint']}", "again", "x") == "throttled"


@pytest.mark.asyncio
async def test_backup_encrypted_restore_to_scratch_only_and_drill_pass(mongo_db):
    out, _ = await _wizard(mongo_db)
    tid = out["tenant_id"]
    m = await backup.backup_tenant(mongo_db, tid, by="test")
    blob = BLOBS[m["path"]]
    assert b"TenantCreated" not in blob and m["counts"]["events"] >= 3 and m["tier"] == "monthly"
    with pytest.raises(ValueError, match="never the live"):
        await backup.restore(mongo_db, m["backup_id"], os.environ["DB_NAME"])
    with pytest.raises(ValueError, match="never the live"):
        await backup.restore(mongo_db, m["backup_id"], mongo_db.name)
    d = await backup.drill(mongo_db, tid, by="test")
    assert d["result"] == "PASS" and d["chain_ok"] and d["counts_ok"], d
    async with use_tenant(tid):
        assert await wrap(mongo_db).events.count_documents({"type": "BackupRestoreDrill", "payload.result": "PASS"}) == 1
    BLOBS[m["path"]] = blob[:-1] + bytes([blob[-1] ^ 1])  # tamper → FAIL recorded
    assert (await backup.drill(mongo_db, tid, by="test"))["result"] == "FAIL"
