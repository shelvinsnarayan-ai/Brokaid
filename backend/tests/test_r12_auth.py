"""R1.2a — real sign-in: invites, password + TOTP, recovery codes, lockout, reset, sessions, bootstrap, production gating."""
import asyncio
import os
import uuid
from datetime import timedelta

import httpx
import pyotp
import pytest

from app.kernel.routes_util import all_routes

from app.auth.sessions import COOKIE_NAME
from app.db.tenant_safe import wrap
from app.kernel import auth_flows as af
from app.kernel.tenant_ctx import use_tenant

PW = "Harbour-Ferry-Lantern-42"


def _db():
    from motor.motor_asyncio import AsyncIOMotorClient
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"] + f"_auth_{uuid.uuid4().hex[:6]}"]


def run(fn, env: dict | None = None):
    async def go():
        from app.main import create_app
        from app.seeds.seed_all_mongo import seed_all_mongo
        db = _db()
        old = {k: os.environ.get(k) for k in (env or {})}
        os.environ.update(env or {})
        try:
            await seed_all_mongo(db)
            app = create_app(mongo_db_override=db, seed_dev_users=False)
            app.state.mongo_db = db
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://t.internal") as c:
                return await fn(db, c, app)
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
            await db.client.drop_database(db.name)
    return asyncio.run(go())


async def _tid(db, slug="harbourline"):
    return (await db.tenants.find_one({"slug": slug}))["tenant_id"]


async def _staff_cookie(db, email):
    from app.auth.dispatch import create_session_scoped
    user, tid = await af.find_user(db, email)
    ctx = await af.login_context(db, user, tid)
    s = await create_session_scoped(db, tid, user_id=user["user_id"], brokerage_id=ctx["brokerage_id"],
                                    active_role=ctx["active_role"], mfa_verified=True)
    return {COOKIE_NAME: s.session_id}


async def _accept(c, token, password=PW):
    r = await c.post(f"/api/auth/invite/{token}/accept", json={"password": password})
    assert r.status_code == 200, r.text
    jar = {COOKIE_NAME: r.cookies[COOKIE_NAME]}
    secret = (await c.post("/api/auth/mfa/setup", cookies=jar)).json()["secret"]
    r = await c.post("/api/auth/mfa/confirm", cookies=jar, json={"code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200, r.text
    return {COOKIE_NAME: r.cookies[COOKIE_NAME]}, secret, r.json()  # R1.2b: the id rotates on verify


async def _events(db, tid, type_):
    async with use_tenant(tid):
        return [e async for e in wrap(db).events.find({"type": type_})]


def test_invite_accept_enrol_and_single_use():
    async def go(db, c, app):
        tid = await _tid(db)
        op = await _staff_cookie(db, "operator@harbourline.example")
        _r = await c.get("/api/o/invites", cookies=op)
        assert _r.status_code == 200, _r.text
        clients = _r.json()["clients"]
        r = await c.post("/api/o/invites", cookies=op, json={"email": "new.ae@harbourline.example", "name": "Nia New", "role": "ae"})
        assert r.status_code == 200 and r.json()["email_status"] == "skipped:example_address".split(":")[0]
        token = r.json()["invite_url"].rsplit("/", 1)[1]
        info = (await c.get(f"/api/auth/invite/{token}")).json()
        assert info["role"] == "Account executive" and info["kind"] == "staff"
        for weak in ("short-pw", "password1234", "Password1234!", "new.ae-is-me-2026"):
            assert (await c.post(f"/api/auth/invite/{token}/accept", json={"password": weak})).status_code == 422
        r = await c.post(f"/api/auth/invite/{token}/accept", json={"password": PW})
        pending = {COOKIE_NAME: r.cookies[COOKIE_NAME]}
        assert (await c.get("/api/o/queue", cookies=pending)).status_code == 401  # no TOTP yet
        assert (await c.post(f"/api/auth/invite/{token}/accept", json={"password": PW})).status_code == 410
        secret = (await c.post("/api/auth/mfa/setup", cookies=pending)).json()["secret"]
        assert (await c.post("/api/auth/mfa/confirm", cookies=pending, json={"code": "000000"})).status_code == 401
        r = await c.post("/api/auth/mfa/confirm", cookies=pending, json={"code": pyotp.TOTP(secret).now()})
        assert r.json()["redirect"] == "/o" and len(r.json()["recovery_codes"]) == 10
        assert (await c.get("/api/o/queue", cookies={COOKIE_NAME: r.cookies[COOKIE_NAME]})).status_code == 200
        assert (await c.get("/api/o/queue", cookies=pending)).status_code == 401  # pending id rotated away
        # client invite → /c, membership with owner authority
        r = await c.post("/api/o/invites", cookies=op, json={"email": "boss@clientco.example", "role": "owner",
                                                            "client_id": clients[0]["client_id"]})
        jar, _, done = await _accept(c, r.json()["invite_url"].rsplit("/", 1)[1])
        assert done["redirect"] == "/c" and (await c.get("/api/c/me", cookies=jar)).status_code == 200
        async with use_tenant(tid):
            u = await wrap(db).users.find_one({"email": "boss@clientco.example"})
            m = await wrap(db).memberships.find_one({"user_id": u["user_id"]})
        ident = await db.auth_identities.find_one({"email": "boss@clientco.example"})
        assert m["role"] == "owner" and m["authority"]["can_sign"] and ident["mfa_secret"] and ident["password_hash"] != PW
        assert not any(k in u for k in af.CRED_FIELDS)  # credentials never live on tenant user docs
        assert len(await _events(db, tid, "InviteCreated")) == 2 and len(await _events(db, tid, "MfaEnrolled")) == 2
        # expired invites are refused
        tok, doc = await af.create_invite(db, tenant_id=tid, email="late@harbourline.example", name="", role="ae",
                                          client_id=None, created_by="t")
        await db.auth_tokens.update_one({"token_id": doc["token_id"]}, {"$set": {"expires_at": af.now() - timedelta(seconds=1)}})
        assert (await c.get(f"/api/auth/invite/{tok}")).status_code == 410
        stored = await db.auth_tokens.find_one({"token_id": doc["token_id"]})
        assert tok not in str(stored) and stored["token_hash"] == af.h(tok)
    run(go)


def test_login_totp_recovery_lockout_and_ledger():
    async def go(db, c, app):
        from app.jobs.chain_verify import verify_tenant
        tid = await _tid(db)
        tok, _ = await af.create_invite(db, tenant_id=tid, email="ae@harbourline.example", name="", role="ae",
                                        client_id=None, created_by="t")
        _, secret, done = await _accept(c, tok)
        r = await c.post("/api/auth/login", json={"email": "AE@harbourline.example", "password": PW})
        assert r.json() == {"next": "totp"}
        jar = {COOKIE_NAME: r.cookies[COOKIE_NAME]}
        assert "httponly" in r.headers["set-cookie"].lower() and "samesite=lax" in r.headers["set-cookie"].lower()
        assert (await c.post("/api/auth/login/verify", cookies=jar, json={"code": "123456"})).status_code == 401
        rc = done["recovery_codes"][0]
        r = await c.post("/api/auth/login/verify", cookies=jar, json={"recovery_code": rc})
        assert r.status_code == 200 and r.json()["redirect"] == "/o"
        s = await db.sessions.find_one({"session_id": r.cookies[COOKIE_NAME]})
        assert timedelta(hours=7, minutes=59) < af._aware(s["expires_at"]) - af.now() <= timedelta(hours=8)
        r = await c.post("/api/auth/login", json={"email": "ae@harbourline.example", "password": PW})
        jar2 = {COOKIE_NAME: r.cookies[COOKIE_NAME]}
        assert (await c.post("/api/auth/login/verify", cookies=jar2, json={"recovery_code": rc})).status_code == 401  # single use
        codes = [(await c.post("/api/auth/login", json={"email": "ae@harbourline.example", "password": "wrong-password-1"},
                               headers={"x-forwarded-for": "10.1.1.1"})).status_code for _ in range(6)]
        assert codes == [401, 401, 401, 429, 429, 429]  # the reused recovery code was failure #1 of 5
        r = await c.post("/api/auth/login", json={"email": "ae@harbourline.example", "password": PW})
        assert r.status_code == 429 and "15 minutes" in r.json()["detail"]
        for t in ("LoginSucceeded", "LoginFailed", "AccountLocked", "RecoveryCodeUsed", "InviteAccepted"):
            assert await _events(db, tid, t), t
        assert (await c.post("/api/auth/login", json={"email": "nobody@nowhere.example", "password": PW})).json()["detail"] \
            == "Email or password is wrong."
        assert await verify_tenant(db, "harbourline")
    run(go)


def test_reset_change_password_and_sessions():
    async def go(db, c, app):
        tid = await _tid(db)
        tok, _ = await af.create_invite(db, tenant_id=tid, email="owner@fictionalclient.example", name="", role="owner",
                                        client_id="00000000-0000-4000-8000-000000000001", created_by="t")
        jar, secret, _ = await _accept(c, tok)
        s = await db.sessions.find_one({"session_id": jar[COOKIE_NAME]})
        assert s["idle_s"] == 12 * 3600 and af._aware(s["expires_at"]) - af.now() > timedelta(days=6, hours=23)
        assert (await c.post("/api/auth/password/forgot", json={"email": "nobody@x.example"})).status_code == 200
        assert (await c.post("/api/auth/password/forgot", json={"email": "owner@fictionalclient.example"})).status_code == 200
        user, _ = await af.find_user(db, "owner@fictionalclient.example")
        rtok, row = await af.issue_token(db, kind="reset", tenant_id=tid, email=user["email"], ttl=af.RESET_TTL, user_id=user["user_id"])
        assert (await c.post("/api/auth/password/reset", json={"token": rtok, "password": "NewHarbour-Pass-77"})).status_code == 200
        assert (await c.post("/api/auth/password/reset", json={"token": rtok, "password": "NewHarbour-Pass-78"})).status_code == 410
        assert (await c.get("/api/c/me", cookies=jar)).status_code == 401  # reset signs out every device
        assert (await c.post("/api/auth/login", json={"email": user["email"], "password": PW})).status_code == 401
        old, _ = await af.issue_token(db, kind="reset", tenant_id=tid, email=user["email"], ttl=timedelta(seconds=-1), user_id=user["user_id"])
        assert (await c.get(f"/api/auth/password/reset/{old}")).status_code == 410

        async def sign_in(pw):
            r = await c.post("/api/auth/login", json={"email": user["email"], "password": pw})
            j = {COOKIE_NAME: r.cookies[COOKIE_NAME]}
            r = await c.post("/api/auth/login/verify", cookies=j, json={"code": pyotp.TOTP(secret).now()})
            return {COOKIE_NAME: r.cookies[COOKIE_NAME]}
        a, b = await sign_in("NewHarbour-Pass-77"), await sign_in("NewHarbour-Pass-77")
        r = await c.post("/api/auth/password/change", cookies=a, json={"current_password": "NewHarbour-Pass-77", "new_password": "Third-Harbour-Pass-9"})
        assert r.json().get("other_sessions_signed_out") == 1, r.text
        assert (await c.get("/api/c/me", cookies=b)).status_code == 401 and (await c.get("/api/c/me", cookies=a)).status_code == 200
        await db.sessions.update_one({"session_id": a[COOKIE_NAME]}, {"$set": {"last_seen_at": af.now() - timedelta(hours=13)}})
        assert (await c.get("/api/c/me", cookies=a)).status_code == 401  # client idle timeout
        c2 = await sign_in("Third-Harbour-Pass-9")
        assert (await c.post("/api/auth/logout", cookies=c2)).status_code == 200
        assert (await c.get("/api/c/me", cookies=c2)).status_code == 401
        assert len(await _events(db, tid, "PasswordChanged")) == 2
    run(go)


def test_invite_permissions_and_mfa_reset():
    async def go(db, c, app):
        tid = await _tid(db)
        ada = await _staff_cookie(db, "principal@harbourline.example")
        cass = await _staff_cookie(db, "ae@harbourline.example")
        assert (await c.post("/api/o/invites", cookies=ada, json={"email": "x@harbourline.example", "role": "operator"})).status_code == 403
        assert (await c.post("/api/o/invites", cookies=ada, json={"email": "y@harbourline.example", "role": "ae"})).status_code == 200
        assert (await c.post("/api/o/invites", cookies=cass, json={"email": "z@harbourline.example", "role": "ae"})).status_code == 403
        assert (await c.post("/api/o/invites", cookies={}, json={"email": "z@harbourline.example", "role": "ae"})).status_code == 401
        assert (await c.post("/api/o/invites", cookies=ada, json={"email": "c@x.example", "role": "owner"})).status_code == 422
        op = await _staff_cookie(db, "operator@harbourline.example")
        r = await c.post("/api/o/invites", cookies=op, json={"email": "w@harbourline.example", "role": "compliance"})
        first = r.json()["invite"]["invite_id"]
        r2 = await c.post(f"/api/o/invites/{first}/reissue", cookies=op)
        assert (await c.get(f"/api/auth/invite/{r.json()['invite_url'].rsplit('/', 1)[1]}")).status_code == 410  # old link dead
        assert (await c.get(f"/api/auth/invite/{r2.json()['invite_url'].rsplit('/', 1)[1]}")).status_code == 200
        tok, _ = await af.create_invite(db, tenant_id=tid, email="deputy@harbourline.example", name="", role="deputy",
                                        client_id=None, created_by="t")
        jar, _, _ = await _accept(c, tok)
        user, _ = await af.find_user(db, "deputy@harbourline.example")
        assert (await c.post(f"/api/o/users/{user['user_id']}/reset-mfa", cookies=cass)).status_code == 403
        assert (await c.post(f"/api/o/users/{user['user_id']}/reset-mfa", cookies=op)).json()["sessions_revoked"] == 1
        assert (await c.get("/api/o/queue", cookies=jar)).status_code == 401
        r = await c.post("/api/auth/login", json={"email": "deputy@harbourline.example", "password": PW})
        assert r.json() == {"next": "enrol"}  # must re-enrol
        assert await _events(db, tid, "MfaReset")
    run(go)


def test_auth_email_gating():
    async def go(db, c, app):
        tid = await _tid(db)
        sent = []

        async def fake(**kw):
            sent.append(kw)
            return "msg-1"
        await db.tenants.update_one({"tenant_id": tid}, {"$set": {"email_settings.allowlist": ["real@owner.test.com"]}})
        kw = dict(kind="invite", name="R", link="https://brokaid.example.com/invite/x", role="Operator", send=fake)
        assert await af.send_auth_email(db, tid, to="kai@fictionalclient.example", **kw) == "skipped:example_address"
        assert await af.send_auth_email(db, tid, to="stranger@gmail.com", **kw) == "skipped:not_allowlisted"
        assert await af.send_auth_email(db, tid, to="real@owner.test.com", **kw) == "sent:msg-1"
        assert "72 hours" in sent[0]["text"] and "password" not in sent[0]["subject"].lower()
        os.environ["EMAIL_AUTH_ENABLED"] = "false"
        try:
            assert await af.send_auth_email(db, tid, to="real@owner.test.com", **kw) == "skipped:auth_email_disabled"
        finally:
            os.environ.pop("EMAIL_AUTH_ENABLED")
    run(go, {"ENVIRONMENT": "production", "AUTH_DEV_MODE": "false", "EMAIL_ENABLED": "false", "CORS_ORIGINS": "https://app.brokaid.example"})


def test_bootstrap_operator_is_idempotent():
    async def go(db, c, app):
        tid = await _tid(db)
        mails = []

        async def fake(**kw):
            mails.append(kw["to"])
            return "m"
        assert await af.bootstrap_operator(db, send=fake) == "issued"
        assert await af.bootstrap_operator(db, send=fake) == "skipped:invite_open"
        inv = await db.auth_tokens.find_one({"bootstrap": True})
        assert inv["role"] == "operator" and inv["tenant_id"] == tid and mails == []  # .example never mailed
        tok, _ = await af.create_invite(db, tenant_id=tid, email=inv["email"], name="", role="operator", client_id=None, created_by="t")
        await _accept(c, tok)
        assert await af.has_active_operator(db, tid)
        await db.auth_tokens.delete_many({})
        assert await af.bootstrap_operator(db, send=fake) == "skipped:operator_exists"
        assert await db.auth_tokens.count_documents({}) == 0
    run(go, {"BOOTSTRAP_OPERATOR_EMAIL": "first.operator@harbourline.example", "BOOTSTRAP_TENANT_SLUG": "harbourline"})


def test_production_has_no_dev_or_legacy_sign_in():
    async def go(db, c, app):
        paths = {getattr(r, "path", "") for r in all_routes(app)}
        assert not any(p.startswith(("/api/auth/dev", "/api/auth/client", "/api/auth/operator")) for p in paths)
        assert "/api/auth/login" in paths and af.cookie_flags()["secure"] is True
        assert (await c.post("/api/auth/dev/impersonate", json={"email": "operator@harbourline.example"})).status_code in (404, 405)
        assert (await c.get("/api/o/queue")).status_code == 401
    run(go, {"ENVIRONMENT": "production", "AUTH_DEV_MODE": "false", "CORS_ORIGINS": "https://app.brokaid.example"})


def test_production_refuses_dev_mode():
    from app.settings import _assert_safe_to_start
    os.environ.update(ENVIRONMENT="production", AUTH_DEV_MODE="true")
    try:
        with pytest.raises(RuntimeError):
            _assert_safe_to_start()
    finally:
        os.environ.update(ENVIRONMENT="development", AUTH_DEV_MODE="true")
