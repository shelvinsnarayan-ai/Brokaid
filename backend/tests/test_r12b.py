"""R1.2b — session rotation, recovery codes, multi-tenant bootstrap/login/switch, demo personas, Google, A02, AE routing, /o/users."""
import uuid

import pyotp
import pytest
from fastapi import HTTPException

from app.auth.sessions import COOKIE_NAME
from app.db.tenant_safe import wrap
from app.kernel import auth_flows as af, notify
from app.kernel.tenant_ctx import use_tenant
from tests.test_r12_auth import PW, _accept, _events, _staff_cookie, _tid, run

CLIENT = "00000000-0000-4000-8000-000000000001"
OTHER_CLIENT = "00000000-0000-4000-8000-0000000000b2"
OTHER_POLICY = "00000000-0000-4000-8000-0000000000b3"


async def _second_tenant(db) -> str:
    tid = str(uuid.uuid4())
    await db.tenants.insert_one({"tenant_id": tid, "slug": "burmac", "display_name": "BurMac Test", "genesis_hash": "0" * 64})
    await db.brokerages.insert_one({"tenant_id": tid, "brokerage_id": str(uuid.uuid4()), "slug": "burmac", "display_name": "BurMac Test"})
    await db.clients.insert_one({"tenant_id": tid, "client_id": OTHER_CLIENT, "display_name": "BurMac Client"})
    await db.policies.insert_one({"tenant_id": tid, "policy_id": OTHER_POLICY, "client_id": OTHER_CLIENT, "status": "managed"})
    return tid


async def _login(c, email, secret, password=PW):
    r = await c.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    pending = {COOKIE_NAME: r.cookies[COOKIE_NAME]}
    r = await c.post("/api/auth/login/verify", cookies=pending, json={"code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200, r.text
    return pending, {COOKIE_NAME: r.cookies[COOKIE_NAME]}, r.json()


def test_session_id_rotates_on_verify_and_recovery_code_single_use():
    async def go(db, c, app):
        tid = await _tid(db)
        tok, _ = await af.create_invite(db, tenant_id=tid, email="ae@harbourline.example", name="", role="ae", client_id=None, created_by="t")
        enrol_pending = {COOKIE_NAME: (await c.post(f"/api/auth/invite/{tok}/accept", json={"password": PW})).cookies[COOKIE_NAME]}
        secret = (await c.post("/api/auth/mfa/setup", cookies=enrol_pending)).json()["secret"]
        r = await c.post("/api/auth/mfa/confirm", cookies=enrol_pending, json={"code": pyotp.TOTP(secret).now()})
        live = {COOKIE_NAME: r.cookies[COOKIE_NAME]}
        assert live != enrol_pending and (await c.get("/api/o/queue", cookies=enrol_pending)).status_code == 401
        assert (await c.post("/api/auth/mfa/setup", cookies=enrol_pending)).status_code == 401
        codes = r.json()["recovery_codes"]
        pending, fresh, _ = await _login(c, "ae@harbourline.example", secret)
        assert pending != fresh
        assert (await c.get("/api/o/queue", cookies=fresh)).status_code == 200
        assert (await c.get("/api/o/queue", cookies=pending)).status_code == 401  # old pending id refused
        assert (await c.post("/api/auth/login/verify", cookies=pending, json={"code": pyotp.TOTP(secret).now()})).status_code == 401
        # recovery code: works once, then refused
        r = await c.post("/api/auth/login", json={"email": "ae@harbourline.example", "password": PW})
        p1 = {COOKIE_NAME: r.cookies[COOKIE_NAME]}
        r = await c.post("/api/auth/login/verify", cookies=p1, json={"recovery_code": codes[3].upper()})
        assert r.status_code == 200 and r.cookies[COOKIE_NAME] != p1[COOKIE_NAME]
        r = await c.post("/api/auth/login", json={"email": "ae@harbourline.example", "password": PW})
        p2 = {COOKIE_NAME: r.cookies[COOKIE_NAME]}
        assert (await c.post("/api/auth/login/verify", cookies=p2, json={"recovery_code": codes[3]})).status_code == 401
        assert (await _events(db, tid, "RecoveryCodeUsed"))[0]["payload"]["remaining"] == 9
    run(go)


def test_multi_tenant_bootstrap_login_chooser_and_switch():
    async def go(db, c, app):
        a, b = await _tid(db), await _second_tenant(db)
        email = "owner.person@synaion.test.com"
        assert await af.bootstrap_operator(db) == "harbourline=issued,burmac=issued"
        assert await af.bootstrap_operator(db) == "harbourline=skipped:invite_open,burmac=skipped:invite_open"
        tokens = {}
        for slug, tid in (("harbourline", a), ("burmac", b)):  # tokens are hashed; issue fresh ones to test acceptance
            tokens[slug], _ = await af.create_invite(db, tenant_id=tid, email=email, name="Owner", role="operator",
                                                     client_id=None, created_by="bootstrap", bootstrap=True)
        jar, secret, done = await _accept(c, tokens["harbourline"])
        assert (await c.get(f"/api/auth/invite/{tokens['burmac']}")).json()["existing_account"] is True
        assert (await c.post(f"/api/auth/invite/{tokens['burmac']}/accept", json={"password": "a-wrong-password-1"})).status_code == 401
        r = await c.post(f"/api/auth/invite/{tokens['burmac']}/accept", json={"password": PW})
        assert r.json() == {"next": "totp"}
        r = await c.post("/api/auth/login/verify", cookies={COOKIE_NAME: r.cookies[COOKIE_NAME]}, json={"code": pyotp.TOTP(secret).now()})
        assert r.status_code == 200 and len(r.json()["tenants"]) == 2
        assert await af.bootstrap_operator(db) == "harbourline=skipped:operator_exists,burmac=skipped:operator_exists"
        _, s_a, out = await _login(c, email, secret)
        assert [t["current"] for t in out["tenants"]] == [False, True] or [t["current"] for t in out["tenants"]] == [True, False]
        current = next(t for t in out["tenants"] if t["current"])["tenant_id"]
        target = b if current == a else a
        r = await c.post("/api/auth/switch-tenant", cookies=s_a, json={"tenant_id": target})
        assert r.status_code == 200 and COOKIE_NAME in r.cookies, (r.status_code, r.text, out)
        s_b = {COOKIE_NAME: r.cookies[COOKIE_NAME]}
        assert s_b != s_a and (await c.get("/api/o/queue", cookies=s_a)).status_code == 401  # switching re-issues
        sess = await db.sessions.find_one({"session_id": s_b[COOKIE_NAME]})
        assert sess["tenant_id"] == target
        listed = {x["client_id"] for x in (await c.get("/api/o/clients", cookies=s_b)).json()["clients"]}
        async with use_tenant(target):
            expected = {x["client_id"] async for x in wrap(db).clients.find({})}
        assert listed <= expected  # the new session lists only the new tenant's clients
        assert (OTHER_CLIENT in listed) == (target == b)
        codes = {target: (await c.get(f"/api/o/client/{OTHER_CLIENT}", cookies=s_b)).status_code}
        r = await c.post("/api/auth/switch-tenant", cookies=s_b, json={"tenant_id": current})
        assert COOKIE_NAME in r.cookies, r.text
        back = {COOKIE_NAME: r.cookies[COOKIE_NAME]}
        codes[current] = (await c.get(f"/api/o/client/{OTHER_CLIENT}", cookies=back)).status_code
        assert codes[a] in (403, 404) and codes[b] not in (401, 403, 404), codes  # A can't see B's client; B can
        s_a2 = back
        assert (await c.post("/api/auth/switch-tenant", cookies=s_a2, json={"tenant_id": str(uuid.uuid4())})).status_code == 404
        # one source of truth: a single identity row; tenant user docs hold no credentials
        assert await db.auth_identities.count_documents({"email": email}) == 1
        async for u in db.users.find({"email": email}):
            assert not any(k in u for k in af.CRED_FIELDS)
    run(go, {"BOOTSTRAP_OPERATOR_EMAIL": "owner.person@synaion.test.com", "BOOTSTRAP_TENANT_SLUG": "harbourline, burmac"})


def test_one_identity_password_and_recovery_code_shared_across_tenants_disable_per_tenant():
    async def go(db, c, app):
        a, b = await _tid(db), await _second_tenant(db)
        email = "both.places@synaion.test.com"
        tok_a, _ = await af.create_invite(db, tenant_id=a, email=email, name="Both", role="ae", client_id=None, created_by="t")
        live_a, secret, done = await _accept(c, tok_a)
        codes = done["recovery_codes"]
        # change the password while in tenant A
        r = await c.post("/api/auth/password/change", cookies=live_a, json={"current_password": PW, "new_password": "Changed-In-Tenant-A-1"})
        assert r.status_code == 200
        # joining tenant B: the OLD password is refused, the new one works (one identity)
        tok_b, _ = await af.create_invite(db, tenant_id=b, email=email, name="Both", role="ae", client_id=None, created_by="t")
        assert (await c.post(f"/api/auth/invite/{tok_b}/accept", json={"password": PW})).status_code == 401
        # use a recovery code in A …
        r = await c.post("/api/auth/login", json={"email": email, "password": "Changed-In-Tenant-A-1"})
        pa = {COOKIE_NAME: r.cookies[COOKIE_NAME]}
        assert (await db.sessions.find_one({"session_id": pa[COOKIE_NAME]}))["tenant_id"] == a
        assert (await c.post("/api/auth/login/verify", cookies=pa, json={"recovery_code": codes[0]})).status_code == 200
        # … then it is refused in B
        r = await c.post(f"/api/auth/invite/{tok_b}/accept", json={"password": "Changed-In-Tenant-A-1"})
        pb = {COOKIE_NAME: r.cookies[COOKIE_NAME]}
        assert (await db.sessions.find_one({"session_id": pb[COOKIE_NAME]}))["tenant_id"] == b
        assert (await c.post("/api/auth/login/verify", cookies=pb, json={"recovery_code": codes[0]})).status_code == 401
        r = await c.post("/api/auth/login/verify", cookies=pb, json={"recovery_code": codes[1]})
        assert r.status_code == 200 and len(r.json()["tenants"]) == 2
        # MFA reset in B applies to the person (re-enrol), Google link is one record
        ident = await db.auth_identities.find_one({"email": email})
        assert sum(1 for x in ident["recovery_codes"] if x["used_at"]) == 2
        # disabling in B is per tenant: still signs in to A, and B is no longer offered
        async with use_tenant(b):
            ub = await wrap(db).users.find_one({"email": email})
            await wrap(db).users.update_one({"user_id": ub["user_id"]}, {"$set": {"disabled": True}})
        _, live, out = await _login(c, email, secret, "Changed-In-Tenant-A-1")
        assert [t["tenant_id"] for t in out["tenants"]] == [a]
        assert (await c.post("/api/auth/switch-tenant", cookies=live, json={"tenant_id": b})).status_code == 404
    run(go)


def test_demo_personas_harbourline_only():
    async def go(db, c, app):
        b = await _second_tenant(db)
        op = await _staff_cookie(db, "operator@harbourline.example")
        r = await c.post("/api/o/invites/demo-personas", cookies=op)
        names = [i["name"] for i in r.json()["invites"]]
        assert names == ["Kai Owner", "Bea Bookkeeper", "Cass Executive", "Ada Principal"] and all("/invite/" in i["invite_url"] for i in r.json()["invites"])
        assert (await c.get("/api/o/invites", cookies=op)).json()["demo_personas_allowed"] is True
        ada = await _staff_cookie(db, "principal@harbourline.example")
        assert (await c.post("/api/o/invites/demo-personas", cookies=ada)).status_code == 403
        with pytest.raises(HTTPException) as e:
            await af.issue_demo_invites(db, b, "x")
        assert e.value.status_code == 403
    run(go)


def test_google_sign_in_matches_invited_email_and_still_needs_totp(monkeypatch):
    async def go(db, c, app):
        tid = await _tid(db)
        tok, _ = await af.create_invite(db, tenant_id=tid, email="ae@harbourline.example", name="", role="ae", client_id=None, created_by="t")
        jar, secret, _ = await _accept(c, tok)
        profiles = {"good": "AE@harbourline.example", "stranger": "nobody@gmail.com"}

        async def fake(session_id):
            return {"email": profiles[session_id].lower(), "name": "x"}
        monkeypatch.setattr(af, "google_profile", fake)
        r = await c.post("/api/auth/google", json={"session_id": "stranger"})
        assert r.status_code == 403 and "Ask your brokerage" in r.json()["detail"]
        r = await c.post("/api/auth/google", json={"session_id": "good"})
        assert r.json() == {"next": "totp"}
        pending = {COOKIE_NAME: r.cookies[COOKIE_NAME]}
        assert (await c.get("/api/o/queue", cookies=pending)).status_code == 401  # TOTP still required
        r = await c.post("/api/auth/login/verify", cookies=pending, json={"code": pyotp.TOTP(secret).now()})
        live = {COOKIE_NAME: r.cookies[COOKIE_NAME]}
        assert (await c.get("/api/o/queue", cookies=live)).status_code == 200
        assert (await _events(db, tid, "LoginSucceeded"))[-1]["payload"]["via"] == "google"
        assert (await c.post("/api/auth/google/link", cookies=live, json={"session_id": "stranger"})).status_code == 409
        assert (await c.post("/api/auth/google/link", cookies=live, json={"session_id": "good"})).status_code == 200
        assert (await c.get("/api/auth/account", cookies=live)).json()["google_email"] == "ae@harbourline.example"
    run(go)


async def _client_session(db, email):
    from app.auth.dispatch import create_session_scoped
    user, tid = await af.find_user(db, email)
    async with use_tenant(tid):
        link = await wrap(db).user_client_links.find_one({"user_id": user["user_id"]})
    s = await create_session_scoped(db, tid, user_id=user["user_id"], client_id=link["client_id"],
                                    active_role=link["role"], mfa_verified=True)
    return {COOKIE_NAME: s.session_id}, user


async def _policy(db, tid, client_id=CLIENT):
    async with use_tenant(tid):
        return (await wrap(db).policies.find_one({"client_id": client_id}))["policy_id"]


def test_a02_deferred_1_2_4_5_6_7_and_every_client_action_checks_authority():
    async def go(db, c, app):
        from app.jobs.chain_verify import verify_tenant
        tid = await _tid(db)
        op = await _staff_cookie(db, "operator@harbourline.example")
        # A02-1: a second Owner (Ada-the-client) sees the same client and her request is attributed to her
        r = await c.post("/api/o/invites", cookies=op, json={"email": "ada.owner@harbourmarine.example", "name": "Ada Owner",
                                                            "role": "owner", "client_id": CLIENT})
        ada, _, _ = await _accept(c, r.json()["invite_url"].rsplit("/", 1)[1])
        kai, kai_u = await _client_session(db, "owner@fictionalclient.example")
        me_kai, me_ada = (await c.get("/api/c/me", cookies=kai)).json(), (await c.get("/api/c/me", cookies=ada)).json()
        assert "memberships" in me_kai and "memberships" in me_ada, (me_kai, me_ada)
        assert {m["client_id"] for m in me_kai["memberships"]} >= {CLIENT} and [m["client_id"] for m in me_ada["memberships"]] == [CLIENT]
        pol = await _policy(db, tid)
        r = await c.post(f"/api/c/matters/{pol}/request-change", cookies=ada, json={"raw_message": "Add a trailer", "idempotency_key": "a02-1"})
        assert r.status_code == 200, r.text
        ev = (await _events(db, tid, "ClientChangeRequested"))[-1]
        ada_user, _ = await af.find_user(db, "ada.owner@harbourmarine.example")
        assert ev["payload"]["submitted_by"] == ada_user["user_id"]
        # A02-2: Kai switches to a second (household) client; acting on a client he isn't linked to is refused
        hh = str(uuid.uuid4())
        async with use_tenant(tid):
            await wrap(db).clients.insert_one({"client_id": hh, "display_name": "Kai household", "type": "household"})
            await wrap(db).user_client_links.insert_one({"user_id": kai_u["user_id"], "client_id": hh, "role": "client_owner"})
            await wrap(db).memberships.insert_one({"membership_id": str(uuid.uuid4()), "client_id": hh, "user_id": kai_u["user_id"],
                                                   "role": "owner", "authority": {}, "revoked_at": None})
            await wrap(db).policies.insert_one({"policy_id": str(uuid.uuid4()), "client_id": hh, "status": "managed", "product": "motor"})
        assert (await c.post("/api/c/switch-client", cookies=kai, json={"client_id": hh})).status_code == 200
        assert (await c.get("/api/c/me", cookies=kai)).json()["session"]["client_id"] == hh
        assert (await c.post("/api/c/switch-client", cookies=ada, json={"client_id": hh})).status_code == 404
        hh_pol = await _policy(db, tid, hh)
        r = await c.post(f"/api/c/matters/{hh_pol}/request-change", cookies=ada, json={"raw_message": "x", "idempotency_key": "leak"})
        assert r.status_code == 404 and "can_request_change" not in r.text  # foreign client: not found, never an authority message
        await c.post("/api/c/switch-client", cookies=kai, json={"client_id": CLIENT})
        # A02-6: nothing leaks across clients
        assert hh not in [m["client_id"] for m in (await c.get("/api/c/me", cookies=ada)).json()["memberships"]]
        # A02-4: Kai turns the bookkeeper's request_change off → ledger before/after → bookkeeper 403
        bea, _ = await _client_session(db, "bookkeeper@fictionalclient.example")
        bea_m = next(m for m in (await c.get("/api/c/me", cookies=bea)).json()["memberships"] if m["client_id"] == CLIENT)
        assert (await c.post(f"/api/c/matters/{pol}/request-change", cookies=bea, json={"raw_message": "Update address", "idempotency_key": "b1"})).status_code == 200
        r = await c.put(f"/api/c/members/{bea_m['membership_id']}/authority", cookies=kai, json={"switch": "can_request_change", "on": False})
        assert r.status_code == 200
        assert (await c.put(f"/api/c/members/{bea_m['membership_id']}/authority", cookies=bea, json={"switch": "can_approve", "on": True})).status_code == 403
        assert (await c.post(f"/api/c/matters/{pol}/request-change", cookies=bea, json={"raw_message": "Again", "idempotency_key": "b2"})).status_code == 403
        ch = (await _events(db, tid, "AuthorityChanged"))[-1]["payload"]
        assert (ch["switch"], ch["before"], ch["after"]) == ("can_request_change", True, False)
        # every client action goes through require_authority: switch everything off → 403 everywhere
        for sw in ("can_claim", "can_certificates", "can_view", "can_pay"):
            await c.put(f"/api/c/members/{bea_m['membership_id']}/authority", cookies=kai, json={"switch": sw, "on": False})
        env = "00000000-0000-4000-8000-00000000e999"
        await af.ledger(db, tid, "EnvelopeOpened", {"kind": "system", "id": "t", "name": "t"}, {"envelope_id": env, "policy_id": pol})
        for method, path, body in (
                ("post", "/api/c/claims", {"description": "Water damage in the office"}),
                ("post", "/api/c/certificate/regen", {"policy_id": pol}),
                ("post", "/api/c/ask/message", {"message": "hello"}),
                ("post", f"/api/c/matters/{pol}/approve/{env}", {}),
                ("post", f"/api/c/matters/{pol}/paid/{env}", {}),
                ("post", f"/api/c/matters/{pol}/sign/{env}", {}),
                ("post", f"/api/c/matters/{pol}/finalise/{env}", {})):
            r = await getattr(c, method)(path, cookies=bea, json=body)
            assert r.status_code == 403 and r.json()["detail"]["code"] == "AuthorityDenied", (path, r.status_code, r.text[:120])
        # A02-5: removing an Owner is blocked; removing the bookkeeper works and ends her session
        ada_m = next(m for m in (await c.get("/api/c/me", cookies=ada)).json()["memberships"] if m["client_id"] == CLIENT)
        r = await c.post(f"/api/c/members/{ada_m['membership_id']}/remove", cookies=kai)
        assert r.status_code == 409 and "second Owner or your broker" in r.json()["detail"]
        assert (await c.post(f"/api/c/members/{bea_m['membership_id']}/remove", cookies=kai)).status_code == 200
        assert (await c.get("/api/c/me", cookies=bea)).status_code == 401
        # A02-7: ledger verifies after all of this
        assert await verify_tenant(db, "harbourline")
    run(go)


def test_client_alerts_go_to_assigned_ae_and_principal_only():
    async def go(db, c, app):
        tid = await _tid(db)
        safe = wrap(db)
        async with use_tenant(tid):
            aes = [r["user_id"] async for r in safe.user_brokerage_roles.find({"role": "ae", "active": True})]
            principals = [r["user_id"] async for r in safe.user_brokerage_roles.find({"role": "principal", "active": True})]
        extra = str(uuid.uuid4())
        async with use_tenant(tid):
            b = await safe.brokerages.find_one({})
            await safe.users.insert_one({"user_id": extra, "email": "second.ae@harbourline.example", "display_name": "Second AE", "kind": "operator"})
            await safe.user_brokerage_roles.insert_one({"user_id": extra, "role": "ae", "active": True, "brokerage_id": b["brokerage_id"]})

        async def recipients(thread):
            await notify.on_event(db, tid, "ClientQuestionAsked", {"client_id": CLIENT, "thread_id": thread}, str(uuid.uuid4()))
            async with use_tenant(tid):
                return {n["user_id"] async for n in safe.notifications.find({"thread_id": thread})}
        everyone = await recipients("t-unassigned")
        assert set(aes + [extra]) <= everyone
        op = await _staff_cookie(db, "operator@harbourline.example")
        w = (await c.get(f"/api/o/clients/{CLIENT}/assignment", cookies=op)).json()
        assert w["assigned"] is None and "every AE" in w["warning"]
        cass = aes[0]
        assert (await c.put(f"/api/o/clients/{CLIENT}/assigned-ae", cookies=op, json={"user_id": cass})).status_code == 200
        assert await recipients("t-assigned") == {cass, *principals}
        kai, _ = await _client_session(db, "owner@fictionalclient.example")
        cass_name = (await c.get("/api/c/broker", cookies=kai)).json()["name"]
        assert cass_name and (await c.get(f"/api/o/clients/{CLIENT}/assignment", cookies=op)).json()["warning"] is None
        cass_cookie = await _staff_cookie(db, "ae@harbourline.example")
        assert (await c.put(f"/api/o/clients/{CLIENT}/assigned-ae", cookies=cass_cookie, json={"user_id": extra})).status_code == 403
        # AE-invited client defaults its AE to the inviter
        async with use_tenant(tid):
            await safe.clients.insert_one({"client_id": "c-new", "display_name": "New Co", "assigned_ae_user_id": None})
        r = await c.post("/api/o/invites", cookies=cass_cookie, json={"email": "boss@newco.example", "role": "owner", "client_id": "c-new"})
        assert r.status_code == 200
        async with use_tenant(tid):
            assert (await safe.clients.find_one({"client_id": "c-new"}))["assigned_ae_user_id"] == cass
        assert (await c.post("/api/o/invites", cookies=cass_cookie, json={"email": "x@harbourline.example", "role": "ae"})).status_code == 403
        assert await _events(db, tid, "ClientAeAssigned")
    run(go)


def test_o_users_admin_actions_and_principal_limits():
    async def go(db, c, app):
        tid = await _tid(db)
        op = await _staff_cookie(db, "operator@harbourline.example")
        ada = await _staff_cookie(db, "principal@harbourline.example")
        tok, _ = await af.create_invite(db, tenant_id=tid, email="deputy@harbourline.example", name="", role="deputy", client_id=None, created_by="t")
        jar, secret, _ = await _accept(c, tok)
        listing = (await c.get("/api/o/users", cookies=ada)).json()
        by = {u["email"]: u for u in listing["users"]}
        dep, olly = by["deputy@harbourline.example"], by["operator@harbourline.example"]
        assert dep["status"] == "active" and dep["mfa"] and olly["editable"] is False and "operator" not in listing["staff_roles"]
        assert (await c.post(f"/api/o/users/{olly['user_id']}/disable", cookies=ada)).status_code == 403
        assert (await c.post(f"/api/o/users/{olly['user_id']}/reset-mfa", cookies=ada)).status_code == 403
        assert (await c.post(f"/api/o/users/{dep['user_id']}/role", cookies=ada, json={"role": "principal"})).status_code == 403
        assert (await c.post(f"/api/o/users/{dep['user_id']}/disable", cookies=ada)).status_code == 200
        assert (await c.get("/api/o/queue", cookies=jar)).status_code == 401
        assert (await c.post("/api/auth/login", json={"email": "deputy@harbourline.example", "password": PW})).status_code == 401
        assert (await c.post(f"/api/o/users/{dep['user_id']}/enable", cookies=ada)).status_code == 200
        _, live, _ = await _login(c, "deputy@harbourline.example", secret)
        r = await c.post(f"/api/o/users/{dep['user_id']}/role", cookies=op, json={"role": "compliance"})
        assert r.json()["after"] == "compliance" and (await c.get("/api/o/queue", cookies=live)).status_code == 401
        assert (await c.post(f"/api/o/users/{dep['user_id']}/reset-mfa", cookies=ada)).status_code == 200
        assert (await c.post(f"/api/o/users/{olly['user_id']}/revoke-sessions", cookies=ada)).status_code == 403
        cass = await _staff_cookie(db, "ae@harbourline.example")
        assert (await c.get("/api/o/users", cookies=cass)).status_code == 403
        for t in ("UserDisabled", "UserEnabled", "UserRoleChanged", "MfaReset"):
            assert await _events(db, tid, t), t
    run(go)
