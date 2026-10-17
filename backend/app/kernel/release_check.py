"""R1.0 — in-app release check: build/config/ledger checks + synthetic vehicle-swap spine through the real routes.

The spine runs in-process (ASGI) with sessions minted for a dedicated, test-flagged synthetic client
"Release Check Pty Ltd" in the Harbourline tenant only. No dev impersonation, no BurMac, no real data.
"""
from __future__ import annotations

import asyncio
import copy
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from pymongo.errors import DuplicateKeyError

from app.auth.dispatch import create_session_scoped
from app.auth.sessions import COOKIE_NAME
from app.db.tenant_safe import wrap
from app.kernel import email_channel, object_store, push_channel
from app.kernel.authority import ROLE_AUTHORITY
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch
from app.seeds.seed_kai_motor_mongo import INSURER_DISPLAY, PREMIUM, PRODUCT, SCHEDULE

log = logging.getLogger("brokaid.release_check")
CLIENT_ID = "00000000-0000-4000-8000-0000000000c1"
CLIENT_NAME = "Release Check Pty Ltd"
USERS = {  # .invalid addresses can never receive email
    "owner": ("release-check-owner@releasecheck.invalid", "Rc Owner", "client"),
    "bookkeeper": ("release-check-bookkeeper@releasecheck.invalid", "Rc Bookkeeper", "client"),
    "ae": ("release-check-ae@releasecheck.invalid", "Release check (system)", "operator"),
}
SWAP = ("Please swap the Hilux ABC-123 on my motor policy for a new 2025 Ford Ranger XLT, "
        "rego XYZ-789, from next Monday.")
SPINE_TIMEOUT_S = 180
LOCK_STALE = timedelta(minutes=6)


class StepFailed(Exception):
    pass


def environment() -> str:
    return (os.environ.get("ENVIRONMENT") or "unknown").strip().lower()


def _is_preview_host() -> bool:
    return "preview.emergentagent.com" in (os.environ.get("APP_BASE_URL") or "")


class Run:
    def __init__(self, run_id: str, inject: str | None):
        self.run_id, self.inject, self.checks = run_id, inject, []

    async def step(self, key: str, label: str, fn, *, soft: bool = False):
        t0 = time.monotonic()
        row = {"key": key, "label": label}
        try:
            if self.inject == key:
                raise StepFailed("Injected failure for testing (not a real fault).")
            out = await fn()
            status, detail = (out if isinstance(out, tuple) else ("pass", out))
        except StepFailed as e:
            status, detail = "fail", str(e)
        except Exception as e:  # noqa: BLE001 — a crash is a failed step, reported plainly
            status, detail = "fail", f"Unexpected error: {type(e).__name__}: {str(e)[:200]}"
        row.update(status=status, detail=detail, ms=round((time.monotonic() - t0) * 1000))
        self.checks.append(row)
        if status == "fail" and not soft:
            raise StepFailed(f"{label}: {detail}")
        return status


# ---- synthetic data ----
async def ensure_synthetic(store, tenant_id: str) -> dict:
    safe, now = wrap(store), datetime.now(timezone.utc)
    ids: dict = {}
    async with use_tenant(tenant_id):
        if not await safe.clients.find_one({"client_id": CLIENT_ID}):
            await safe.clients.insert_one({"client_id": CLIENT_ID, "display_name": CLIENT_NAME, "industry": "synthetic",
                                           "domicile": "Sydney, AU", "jurisdiction_code": "AU", "test": True,
                                           "release_check": True})
        brokerage = await safe.brokerages.find_one({})
        for key, (email, name, kind) in USERS.items():
            u = await safe.users.find_one({"email": email})
            if not u:
                u = {"user_id": str(uuid.uuid4()), "email": email, "display_name": name, "kind": kind,
                     "password_hash": None, "mfa_secret": None, "fictional": True, "test": True, "created_at": now}
                try:
                    await safe.users.insert_one(dict(u))
                except DuplicateKeyError:
                    u = await safe.users.find_one({"email": email})
            ids[key] = u["user_id"]
        for key, role, mrole in (("owner", "client_owner", "owner"), ("bookkeeper", "client_contact", "bookkeeper")):
            uid = ids[key]
            if not await safe.user_client_links.find_one({"user_id": uid, "client_id": CLIENT_ID}):
                await safe.user_client_links.insert_one({"user_id": uid, "tenant_id": tenant_id, "client_id": CLIENT_ID, "role": role})
            if not await safe.memberships.find_one({"user_id": uid, "client_id": CLIENT_ID}):
                await safe.memberships.insert_one({
                    "membership_id": str(uuid.uuid4()), "client_id": CLIENT_ID, "user_id": uid, "role": mrole,
                    "authority": ROLE_AUTHORITY[mrole], "created_at": now, "created_by": "release_check",
                    "revoked_at": None, "revoked_by": None})
        if not await safe.user_brokerage_roles.find_one({"user_id": ids["ae"], "role": "ae"}):
            await safe.user_brokerage_roles.insert_one({"user_id": ids["ae"], "tenant_id": tenant_id,
                                                        "brokerage_id": brokerage["brokerage_id"], "role": "ae", "active": True})
    ids["brokerage_id"] = brokerage["brokerage_id"]
    return ids


async def fresh_policy(store, tenant_id: str, run_id: str) -> str:
    """Each run gets its own policy from the canonical seed, so the seed schedule is always intact."""
    policy_id, now = str(uuid.uuid4()), datetime.now(timezone.utc)
    async with use_tenant(tenant_id):
        await wrap(store).policies.insert_one({
            "policy_id": policy_id, "client_id": CLIENT_ID, "status": "managed", "product": PRODUCT,
            "insurer_display": INSURER_DISPLAY, "inception": now - timedelta(days=30), "expiry": now + timedelta(days=335),
            "premium_amount": PREMIUM, "premium_currency": "AUD", "premium_status": "current",
            "schedule": copy.deepcopy(SCHEDULE), "imported_from": None, "fictional": True, "test": True,
            "release_check_run": run_id})
    await append_via_dispatch(store, tenant_id, "PolicyCreated",
                              actor={"kind": "system", "id": "release_check", "name": "release-check"},
                              payload={"policy_id": policy_id, "client_id": CLIENT_ID, "status": "managed", "product": PRODUCT,
                                       "insurer_display": INSURER_DISPLAY, "premium_amount": PREMIUM, "premium_currency": "AUD",
                                       "schedule": copy.deepcopy(SCHEDULE), "mocked": True, "fictional": True, "test": True},
                              idempotency_key=f"release_check:policy:{policy_id}")
    return policy_id


async def _sessions(store, tenant_id: str, ids: dict) -> dict:
    mk = lambda **kw: create_session_scoped(store, tenant_id, mfa_verified=True, ttl=timedelta(minutes=10), **kw)  # noqa: E731
    return {
        "owner": (await mk(user_id=ids["owner"], client_id=CLIENT_ID, active_role="client_owner")).session_id,
        "bookkeeper": (await mk(user_id=ids["bookkeeper"], client_id=CLIENT_ID, active_role="client_contact")).session_id,
        "ae": (await mk(user_id=ids["ae"], brokerage_id=ids["brokerage_id"], active_role="ae")).session_id,
    }


async def _envelope_events(store, tenant_id: str, env: str) -> dict:
    async with use_tenant(tenant_id):
        return {e["type"]: e async for e in wrap(store).events.find({"payload.envelope_id": env}).sort("seq", 1)}


# ---- the run ----
async def execute(app, store, tenant_id: str, slug: str, run_id: str, inject: str | None) -> dict:
    from app.jobs.chain_verify import verify_tenant
    from app.routes import build_info

    r, started = Run(run_id, inject), datetime.now(timezone.utc)
    info = await build_info.build_info()
    env = environment()

    async def build():
        if not info.get("sha"):
            raise StepFailed("The build has no commit id (sha). Redeploy from a committed build.")
        fresh = build_info._resolve_identity().get("sha")
        if fresh and fresh != info["sha"]:
            raise StepFailed(f"Running build {info['sha'][:7]} doesn't match the deployed build {fresh[:7]}.")
        if env == "production" and info.get("auth_dev_mode"):
            raise StepFailed("Dev login is switched on in production (AUTH_DEV_MODE must be false).")
        if env != "production" and not _is_preview_host():
            raise StepFailed(f"This doesn't look like preview but ENVIRONMENT is '{env}', not 'production'.")
        return f"Build {info['sha'][:7]} on {env}; dev login {'on' if info.get('auth_dev_mode') else 'off'}."

    async def email_cfg():
        on = email_channel.email_enabled()
        return "info", f"Email is {'on' if on else 'off'}{' (expected off in production)' if env == 'production' else ''}."

    async def vapid():
        return ("pass", "Push keys present.") if push_channel.configured() else ("warn", "Push keys (VAPID) are missing; push alerts won't send.")

    async def storage():
        path = f"{object_store.APP_NAME}/release-check/ping.txt"
        await object_store.put_object(path, run_id.encode(), "text/plain")
        data, _ = await object_store.get_object(path)
        if data.decode() != run_id:
            raise StepFailed("File storage returned the wrong content.")
        return "File storage write and read OK."

    async def llm():
        if not os.environ.get("EMERGENT_LLM_KEY"):
            raise StepFailed("The AI key (EMERGENT_LLM_KEY) is missing.")
        return "AI key present."

    async def ledger():
        if not await verify_tenant(store, slug):
            raise StepFailed("The ledger hash chain did not verify.")
        return "Ledger hash chain verified."

    async def queue():
        from app.kernel import jobs
        s = await jobs.summary(store, tenant_id)
        if not s["leader"]["alive"]:
            raise StepFailed("No background scheduler is running (leader lease expired).")
        if s["oldest_queued_s"] > 300:
            raise StepFailed(f"Background work is backed up: oldest job has waited {s['oldest_queued_s'] // 60} minutes.")
        old_dead = [d for d in s["dead"] if d["finished_at"] and
                    datetime.fromisoformat(d["finished_at"]) < datetime.now(timezone.utc) - timedelta(hours=1)]
        if old_dead:
            return "warn", f"{len(old_dead)} failed background job(s) older than an hour need a look in Background work."
        return f"Queue healthy: {s['counts']['queued']} queued, {s['counts']['running']} running, {s['counts']['dead']} dead."

    async def auth_cfg():
        from app.kernel import auth_flows
        paths = {getattr(rt, "path", "") for rt in app.routes}
        if env == "production" and any(p.startswith(("/api/auth/dev", "/api/auth/client", "/api/auth/operator")) for p in paths):
            raise StepFailed("Dev or legacy sign-in routes are reachable in production.")
        f = auth_flows.cookie_flags()
        if not (f["httponly"] and f["samesite"] == "lax" and (f["secure"] or env != "production")):
            raise StepFailed(f"Session cookie flags are wrong: {f}.")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://release-check.internal") as h:
            code = (await h.get("/api/o/queue")).status_code
        if code != 401:
            raise StepFailed(f"Signed-out access to staff pages returned {code}, not 401.")
        return (f"Sign-in checks OK: dev login {'off' if not info.get('auth_dev_mode') else 'on (preview)'}, "
                f"cookie HttpOnly/SameSite=Lax/{'Secure' if f['secure'] else 'not Secure'}, signed-out staff page → 401.")

    for key, label, fn in (("build", "Build", build), ("auth", "Sign-in", auth_cfg), ("jobs", "Background work", queue), ("config.email", "Email setting", email_cfg),
                           ("config.push", "Push keys", vapid), ("config.storage", "File storage", storage),
                           ("config.llm", "AI key", llm), ("ledger", "Ledger chain", ledger)):
        await r.step(key, label, fn, soft=True)

    ids = await ensure_synthetic(store, tenant_id)
    sess = await _sessions(store, tenant_id, ids)
    env_id: str | None = None
    policy_id = await fresh_policy(store, tenant_id, run_id)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://release-check.internal", timeout=60) as http:
        def as_(who):
            return {"Cookie": f"{COOKIE_NAME}={sess[who]}"}

        async def expect(resp, code, what):
            if resp.status_code != code:
                raise StepFailed(f"{what} returned {resp.status_code}, expected {code}: {resp.text[:160]}")
            return resp

        async def spine():
            nonlocal env_id
            base = f"/api/c/matters/{policy_id}"

            async def request():
                nonlocal env_id
                resp = await expect(await http.post(f"{base}/request-change", headers=as_("owner"),
                                                    json={"raw_message": SWAP, "idempotency_key": f"release-check:{run_id}"}),
                                    200, "Change request")
                env_id = resp.json()["envelope_id"]
                async with use_tenant(tenant_id):  # flag as test before any counts or alerts see it
                    await wrap(store).test_flags.insert_one({"kind": "envelope", "ref": env_id, "reason": "release_check",
                                                             "flagged_at": datetime.now(timezone.utc)})
                return f"Request received (envelope {env_id[:8]})."

            async def classify_quote():
                deadline = time.monotonic() + 150
                while time.monotonic() < deadline:
                    evs = await _envelope_events(store, tenant_id, env_id)
                    if "RequestNeedsHumanReview" in evs:
                        why = (evs["RequestNeedsHumanReview"].get("payload") or {}).get("reason") or "parked"
                        raise StepFailed(f"The request was parked for a person instead of quoted ({why}).")
                    if "EndorsementQuoteReady" in evs:
                        tier = (evs.get("AdviceBoundaryClassified", {}).get("payload") or {}).get("tier")
                        amt = (evs.get("CarrierQuoted", {}).get("payload") or {}).get("premium_change_amount")
                        if tier != "F":
                            raise StepFailed(f"Classified as tier {tier}, expected F.")
                        if str(amt) not in ("85.00", "85", "85.0"):
                            raise StepFailed(f"Quote was A${amt}, expected A$85.")
                        return "Classified F; quoted A$85.00."
                    await asyncio.sleep(1.5)
                raise StepFailed("No quote within 150 seconds.")

            async def broker():
                await expect(await http.post(f"/api/o/queue/{env_id}/approve", headers=as_("ae"),
                                             json={"reason": "Release check"}), 200, "Broker approval")
                st = (await http.get(f"{base}/status", headers=as_("owner"))).json().get("state")
                if st != "awaiting_client_approval":
                    raise StepFailed(f"After broker approval the client sees '{st}', expected awaiting_client_approval.")
                return "Broker approved; client sees 'ready for your approval'."

            async def probe_bookkeeper():
                await expect(await http.post(f"{base}/approve/{env_id}", headers=as_("bookkeeper")), 403, "Bookkeeper approve")
                return "Bookkeeper can't approve (403)."

            async def client_approve():
                await expect(await http.post(f"{base}/approve/{env_id}", headers=as_("owner")), 200, "Client approval")
                return "Client approved explicitly."

            async def paid():
                await expect(await http.post(f"{base}/paid/{env_id}", headers=as_("owner"), json={}), 200, "Payment advice")
                return "payment.advised recorded."

            async def bank_receipt():
                from decimal import Decimal

                from app.kernel import bankfeed
                from app.scripts import bank_synth
                async with use_tenant(tenant_id):
                    pay = await wrap(store).payables.find_one({"envelope_id": env_id})
                if not pay:
                    raise StepFailed("No payment reference (CRN) was issued for the synthetic change.")
                if pay["state"] != "advised":
                    raise StepFailed(f"After 'I've paid this' the item is '{pay['state']}', expected advised (not received).")
                line = [{"date": datetime.now(timezone.utc).date(), "amount": Decimal(pay["amount"]),
                         "description": f"BPAY {pay['crn']} RELEASE CHECK {run_id[:8]}", "balance": Decimal(pay["amount"])}]
                res = await bankfeed.import_statement(store, tenant_id, data=bank_synth.cba(line).encode(), filename="release-check.csv",
                                                      user_id="release-check", storage_path=None, test=True)
                async with use_tenant(tenant_id):
                    txn = await wrap(store).bank_txns.find_one({"statement_id": res["statement"]["statement_id"]})
                    pay = await wrap(store).payables.find_one({"envelope_id": env_id})
                    ev = await wrap(store).events.find_one({"type": "PremiumReceived", "payload.txn_fingerprint": txn["fingerprint"]})
                if txn["status"] != "matched" or pay["state"] != "received" or not ev:
                    raise StepFailed(f"The CRN line didn't auto-match (line {txn['status']}, item {pay['state']}).")
                return f"Bank line with CRN {pay['crn']} auto-matched; PremiumReceived recorded (test-flagged)."

            async def sign():
                await expect(await http.post(f"{base}/sign/{env_id}", headers=as_("owner")), 200, "Signature")
                return "Signed."

            async def packet():
                resp = await expect(await http.get(f"/api/o/envelope/{env_id}/change-packet", headers=as_("ae")), 200, "Change packet")
                if "XYZ-789" not in resp.text:
                    raise StepFailed("The change packet doesn't show the new vehicle XYZ-789.")
                return "Change packet issued."

            token: dict = {}

            async def finalise():
                resp = await expect(await http.post(f"{base}/finalise/{env_id}", headers=as_("owner")), 200, "Finalise")
                token["t"] = resp.json().get("verifier_token")
                if not resp.json().get("certificate") or not token["t"]:
                    raise StepFailed("Finalise returned no certificate.")
                return "Endorsement executed; certificate issued."

            async def verifier():
                resp = await expect(await http.get(f"/api/verifier/{token['t']}"), 200, "Public verifier")
                body = resp.json()
                if body.get("verdict") != "VALID":
                    raise StepFailed(f"Verifier said {body.get('verdict')}, expected VALID.")
                if "XYZ-789" not in resp.text:
                    raise StepFailed("The verified certificate doesn't show the new vehicle XYZ-789.")
                return "Public verifier: VALID, shows XYZ-789."

            for key, label, fn in (("spine.request", "Change request", request),
                                   ("spine.quote", "Classify F and quote A$85", classify_quote),
                                   ("spine.broker", "Broker approval", broker),
                                   ("access.bookkeeper", "Bookkeeper can't approve", probe_bookkeeper),
                                   ("spine.client_approval", "Client approval", client_approve),
                                   ("spine.paid", "Payment advised", paid),
                                   ("money.bank_receipt", "Bank statement → CRN match → received", bank_receipt),
                                   ("spine.sign", "Sign", sign),
                                   ("spine.packet", "Change packet", packet),
                                   ("spine.finalise", "Finalise and certificate", finalise),
                                   ("spine.verifier", "Public verifier", verifier)):
                await r.step(key, label, fn)

        try:
            await asyncio.wait_for(spine(), SPINE_TIMEOUT_S)
        except StepFailed:
            pass
        except asyncio.TimeoutError:
            r.checks.append({"key": "spine.timeout", "label": "Spine", "status": "fail", "ms": SPINE_TIMEOUT_S * 1000,
                             "detail": f"The synthetic change didn't finish within {SPINE_TIMEOUT_S // 60} minutes."})
        try:
            await renewal_steps(r, http, as_, expect, store, tenant_id, ids, run_id)
        except StepFailed:
            pass
        await manual_only_steps(r, store)
        await compliance_steps(r, store, tenant_id, ids)
        await claim_steps(r, store, tenant_id, ids, run_id)

        async def probe_client_on_o():
            await expect(await http.get("/api/o/queue", headers=as_("owner")), 403, "Client on broker route")
            return "Client can't open broker routes (403)."

        async def probe_ae_governance():
            await expect(await http.get("/api/o/kill-switch", headers=as_("ae")), 403, "AE on governance")
            return "AE can't open agent operations (403)."

        async def probe_tenant_binding():
            # tenant-safe: system-only — another tenant's client/policy whose ids don't also exist here.
            async def foreign(coll, key):
                async for d in store[coll].find({"tenant_id": {"$ne": tenant_id}}, {key: 1}):
                    if not await store[coll].find_one({"tenant_id": tenant_id, key: d[key]}):
                        return d[key]
                return None
            cid, pid = await foreign("clients", "client_id"), await foreign("policies", "policy_id")
            probes = [(w, p) for w, p, ok in (("ae", f"/api/o/client/{cid}", cid), ("owner", f"/api/c/matters/{pid}/status", pid)) if ok]
            if not probes:
                # Seeds reuse client ids across tenants: prove a shared id resolves to THIS tenant's record only.
                async for d in store.clients.find({"tenant_id": {"$ne": tenant_id}}, {"client_id": 1, "display_name": 1}):
                    mine = await store.clients.find_one({"tenant_id": tenant_id, "client_id": d["client_id"]})
                    if mine and mine.get("display_name") != d.get("display_name"):
                        body = (await http.get(f"/api/o/client/{d['client_id']}", headers=as_("ae"))).text
                        if d["display_name"] in body:
                            raise StepFailed("A session for this tenant saw another tenant's client record.")
                        return "Sessions are bound to one tenant: a client id shared by two tenants shows only this tenant's record."
                return "warn", "Only one tenant has data; tenant binding not exercised."
            for who, path in probes:
                code = (await http.get(path, headers=as_(who))).status_code
                if code not in (403, 404):
                    raise StepFailed(f"A {who} session for this tenant read another tenant's data ({path} → {code}).")
            return f"Sessions are bound to one tenant: {len(probes)} other-tenant probe(s) → 403/404."

        await r.step("access.client_on_o", "Client can't open broker routes", probe_client_on_o, soft=True)
        await r.step("access.ae_governance", "AE can't open agent operations", probe_ae_governance, soft=True)
        await r.step("access.tenant_binding", "Session bound to one tenant", probe_tenant_binding, soft=True)
        if env_id:
            await r.step("notifications", "Alerts stay test-only", lambda: _check_alerts(store, tenant_id, env_id, policy_id, ids, started), soft=True)
        await _cleanup(http, as_, store, tenant_id, env_id, policy_id, ids, sess)
    result = "FAIL" if any(c["status"] == "fail" for c in r.checks) else "PASS"
    return {"sha": info.get("sha"), "short_sha": (info.get("sha") or "")[:7], "env": env, "result": result,
            "checks": r.checks, "envelope_id": env_id, "policy_id": policy_id}


async def renewal_steps(r, http, as_, expect, store, tenant_id, ids, run_id) -> None:
    """R1.6: a synthetic renewal on a fresh test policy with an injected clock (T-60 → bound → verifier VALID)."""
    from decimal import Decimal

    from app.kernel import bankfeed, renewals as rn
    from app.scripts import bank_synth
    pid = await fresh_policy(store, tenant_id, f"{run_id}:renewal")
    pol = await rn._policy(store, tenant_id, pid)
    exp = rn._utc(pol["expiry"])
    t0 = exp - timedelta(days=60)
    st: dict = {}
    kw = {"by": ids["ae"], "role": "ae"}

    async def start():
        from app.kernel import compliance_docs
        await compliance_docs.set_client_type(store, tenant_id, pol["client_id"], "wholesale",
                                              "Synthetic release-check business client", "release-check")
        if (await rn.tick(store, tenant_id, t0, [pid]))["started"] != 1:
            raise StepFailed("The renewal didn't start at T-60 (injected clock).")
        async with use_tenant(tenant_id):
            st["r"] = await wrap(store).renewals.find_one({"policy_id": pid}, {"_id": 0})
        st["r"] = await rn.request_info(store, tenant_id, st["r"], now=t0, **kw)
        st["base"] = f"/api/c/renewals/{st['r']['renewal_id']}"
        return "Started at T-60 (injected clock); checklist sent."

    async def info():
        await expect(await http.post(f"{st['base']}/info", headers=as_("owner"),
                                     json={"answers": {"claims": {"any": False}, "turnover": {"value": "500000"}}}), 200, "Renewal info")
        return "Client answered the checklist."

    async def market():
        r0 = await rn.get(store, tenant_id, st["r"]["renewal_id"])
        names = (await rn.insurers(store, tenant_id))[:3]
        r0 = await rn.request_quotes(store, tenant_id, r0, names, now=t0, **kw)
        exc = str((pol["schedule"].get("excess") or {}).get("any_one_occurrence") or 0)
        r0 = await rn.manual_quote(store, tenant_id, r0, {"insurer": "Release Check Mutual", "premium": "1111.00", "excess": exc},
                                   by_name="release check", now=t0, **kw)
        st["q"] = r0["quotes"][-1]["quote_id"]
        r0 = await rn.recommend(store, tenant_id, r0, st["q"], "Lowest premium with the same cover and excess as expiring.",
                                ["Lowest premium"], now=t0, **kw)
        await rn.send_to_client(store, tenant_id, r0, now=t0, **kw)
        return f"{len(names)} MOCKED quote(s) + 1 manual quote; recommendation (tier {r0['recommendation']['tier']}) sent."

    async def decide():
        await expect(await http.post(f"{st['base']}/decide", headers=as_("bookkeeper"), json={"choice": st["q"]}), 403, "Bookkeeper decide")
        d = await expect(await http.post(f"{st['base']}/decide", headers=as_("owner"), json={"choice": st["q"]}), 200, "Client decide")
        st["crn"] = d.json()["payment"]["crn"]
        await expect(await http.post(f"{st['base']}/paid", headers=as_("owner")), 200, "Renewal payment advice")
        return f"Bookkeeper 403; owner approved; CRN {st['crn']} advised."

    async def received():
        line = [{"date": datetime.now(timezone.utc).date(), "amount": Decimal("1111.00"),
                 "description": f"BPAY {st['crn']} RENEWAL CHECK {run_id[:8]}", "balance": Decimal("1111.00")}]
        await bankfeed.import_statement(store, tenant_id, data=bank_synth.cba(line).encode(), filename="release-check-renewal.csv",
                                        user_id="release-check", storage_path=None, test=True)
        if (await rn.get(store, tenant_id, st["r"]["renewal_id"]))["stage"] != "paid":
            raise StepFailed("The CRN statement line didn't move the renewal to Paid.")
        return "Bank line → Received → Paid."

    async def bound():
        s = await expect(await http.post(f"{st['base']}/sign", headers=as_("owner")), 200, "Renewal sign")
        v = await expect(await http.get(f"/api/verifier/{s.json()['verifier_token']}"), 200, "Renewal verifier")
        if s.json()["stage"] != "bound" or v.json().get("verdict") != "VALID" or not str(v.json().get("effective_from", "")).startswith(exp.date().isoformat()):
            raise StepFailed(f"Renewal ended at {s.json()['stage']}; verifier {v.json().get('verdict')} from {v.json().get('effective_from')}.")
        return f"Bound; new certificate VALID from {exp.date():%d/%m/%Y}."

    for key, label, fn in (("renewal.start", "Renewal starts at T-60", start), ("renewal.info", "Renewal checklist", info),
                           ("renewal.market", "Quotes and recommendation", market), ("renewal.decide", "Client decision", decide),
                           ("renewal.received", "Renewal premium received", received), ("renewal.bound", "Renewal bound", bound)):
        await r.step(key, label, fn)


async def manual_only_steps(r, store) -> None:
    """R1.6 ruling: in a manual_only (real) tenant, no MOCKED quote or MOCKED payment details are produced."""
    from app.kernel import holidays, payments, renewals as rn

    async def check():
        # tenant-safe: system-only — list tenants to find the manual_only ones
        tids = [t["tenant_id"] async for t in store.tenants.find({}, {"tenant_id": 1})]
        manual = [t for t in tids if await holidays.quoting_mode(store, t) == "manual_only"]
        if not manual:
            return "warn", "No manual_only tenant exists, so this wasn't exercised."
        for tid in manual:
            safe = wrap(store)
            d = payments.details({"amount": "1.00", "currency": "AUD", "due_date": "2026-01-01", "crn": "0", "state": "owed"},
                                 await payments.get_settings(store, tid), "manual_only")
            if d.get("mocked") or any((d.get(k) or {}).get("mocked") for k in ("bpay", "eft")):
                raise StepFailed("A manual_only tenant would show MOCKED payment details.")
            try:
                await rn.request_quotes(store, tid, {}, ["x"], by="release-check", role="release-check")
                raise StepFailed("The mocked carrier channel accepted a renewal quote request in a manual_only tenant.")
            except rn.Bad:
                pass
            async with use_tenant(tid):
                mocked_r = await safe.renewals.count_documents({"quotes.mocked": True})
                mocked_q = await safe.events.count_documents({"type": {"$in": ["CarrierQuoted", "RenewalQuoteRecorded"]},
                                                              "payload.mocked": True, "ts": {"$gte": QUOTING_MODE_SINCE}})
            if mocked_r or mocked_q:
                raise StepFailed(f"A manual_only tenant has {mocked_r + mocked_q} MOCKED quote(s).")
        return f"{len(manual)} manual_only tenant(s): no MOCKED quotes or payment details; mocked channel refused."

    await r.step("quoting.manual_only", "Real tenants: no mocked quotes or payment details", check)


QUOTING_MODE_SINCE = "2026-10-17T21:00:00"  # R1.6 deploy: seeded demo events before this are archived history


async def compliance_steps(r, store, tenant_id, ids) -> None:
    """R1.7: FSG delivery on the synthetic client's invite, the complaint clock, and the AI eval gate."""
    from app.eval import gate
    from app.kernel import complaints, compliance_docs

    async def fsg():
        d = await compliance_docs.on_client_onboarded(store, tenant_id, CLIENT_ID, ids["owner"], "invite_acceptance")
        if not d:
            return "warn", "No FSG is uploaded for this brokerage, so none could be delivered (DocumentDeliveryMissing recorded)."
        async with use_tenant(tenant_id):
            ev = await wrap(store).events.find_one({"type": "DocumentDelivered", "payload.delivery_id": d["delivery_id"]})
        if not ev:
            raise StepFailed("FSG delivery has no DocumentDelivered ledger event.")
        return f"FSG v{d['version']} delivered on invite acceptance (sha {d['sha256'][:10]}…)."

    async def clock():
        c = complaints.clock(datetime(2026, 12, 24, 3, tzinfo=timezone.utc), "NSW")  # Thu 24 Dec 2pm Sydney
        if c["ack_due"].date().isoformat() != "2026-12-29" or c["response_due"].date().isoformat() != "2027-01-23":
            raise StepFailed(f"Complaint clock wrong: ack {c['ack_due']}, response {c['response_due']}.")
        return "Received 24/12/2026 → acknowledge by 29/12 (Christmas + Boxing Day skipped), respond by 23/01/2027."

    async def evalgate():
        st = await gate.status(store)
        if not st["last"]:
            return "warn", "No AI eval run recorded yet."
        if not st["current"]:
            return "warn", f"The last eval ({st['last']['prompt_hash'][:10]}) is older than the current prompts ({st['prompt_hash'][:10]})."
        if not st["last"]["gate_pass"]:
            raise StepFailed(f"The last eval failed the gate: {st['last']['summary']}.")
        return f"Eval gate PASS for prompt {st['prompt_hash'][:10]}: {st['last']['summary']}."

    for key, label, fn in (("compliance.fsg", "FSG delivered on invite", fsg), ("compliance.complaint_clock", "Complaint clock", clock),
                           ("ai.eval_gate", "AI eval gate", evalgate)):
        await r.step(key, label, fn)


async def claim_steps(r, store, tenant_id, ids, run_id) -> None:
    """R1.8: a synthetic motor claim lodge → submitted (MOCKED here) → accepted → settled → closed, test-flagged;
    and no MOCKED insurer claim number in a manual_only tenant."""
    from app.kernel import claims as ck, holidays

    async def lifecycle():
        pid = await fresh_policy(store, tenant_id, f"{run_id}:claim")
        async with use_tenant(tenant_id):
            pol = await wrap(store).policies.find_one({"policy_id": pid}, {"_id": 0})
        rego = ((pol.get("schedule") or {}).get("vehicles") or [{}])[0].get("rego")
        c = await ck.lodge(store, tenant_id, client_id=CLIENT_ID, policy=pol, by=ids["owner"], role="client", channel="in_app", test=True,
                           body={"claim_class": "motor", "subtype": "accident", "incident_date": datetime.now(timezone.utc).date().isoformat(),
                                 "where": "Release check car park", "description": "Synthetic release-check bump, rear bumper scratched.",
                                 "injuries": False, "safe": True, "rego": rego})
        if any(f["code"] == "asset_not_on_schedule" for f in c["flags"]):
            raise StepFailed("The scheduled vehicle was flagged as not on the schedule.")
        await ck.acknowledge(store, tenant_id, c, by="release-check", role="ae")
        await ck.submit_mocked(store, tenant_id, c, by="release-check", role="ae")
        letter = await ck.add_file(store, tenant_id, c, kind="decision_letter", data=b"%PDF-1.4 release check decision letter",
                                   filename="decision.pdf", content_type="application/pdf", keep_location=False, by="release-check",
                                   role="ae", client_visible=True)
        await ck.record_decision(store, tenant_id, c, outcome="accepted", reason="Synthetic", letter_file_id=letter["file_id"],
                                 amount="500", by="release-check", role="ae")
        await ck.settle(store, tenant_id, c, amount="500", excess_applied="0", payee="repairer", payee_name="Synthetic Repairs",
                        method="insurer_direct", txn_id=None, by="release-check", role="ae")
        await ck.close(store, tenant_id, c, note="Release check", by="release-check", role="ae")
        async with use_tenant(tenant_id):
            n = await wrap(store).events.count_documents({"type": "ClaimStageChanged", "payload.claim_id": c["claim_id"]})
        if c["stage"] != "closed" or n != 5:
            raise StepFailed(f"Claim ended at {c['stage']} with {n} stage events (expected closed, 5: triage, submitted, decision, settlement, closed).")
        return f"Claim {c['claim_reference']} (test): lodged → triage → submitted (MOCKED {c['insurer_claim']['claim_no']}) → accepted → settled → closed."

    async def manual_only():
        # tenant-safe: system-only — list tenants to find the manual_only ones
        tids = [t["tenant_id"] async for t in store.tenants.find({}, {"tenant_id": 1})]
        manual = [t for t in tids if await holidays.quoting_mode(store, t) == "manual_only"]
        for tid in manual:
            try:
                await ck.submit_mocked(store, tid, {"claim_id": "release-check", "stage": "triage"}, by="release-check", role="ae")
                raise StepFailed("The MOCKED insurer claim channel accepted a claim in a manual_only tenant.")
            except ck.Bad:
                pass
            async with use_tenant(tid):
                n = await wrap(store).events.count_documents({"type": "ClaimSubmittedToInsurer", "payload.mocked": True})
            if n:
                raise StepFailed(f"A manual_only tenant has {n} MOCKED insurer claim number(s).")
        return f"{len(manual)} manual_only tenant(s): MOCKED insurer claim channel refused; no MOCKED claim numbers."

    await r.step("claims.lifecycle", "Synthetic motor claim", lifecycle)
    await r.step("claims.manual_only", "Real tenants: no mocked insurer claim no.", manual_only)


def _alert_query(env_id: str, policy_id: str, ids: dict, since: datetime | None = None) -> dict:
    q = {"created_at": {"$gte": since}} if since else {}
    return {**q, "$or": [{"dedupe_key": {"$regex": env_id}}, {"link": {"$regex": policy_id}},
                    {"user_id": {"$in": [ids["owner"], ids["bookkeeper"], ids["ae"]]}}]}


async def _check_alerts(store, tenant_id, env_id, policy_id, ids, since):
    rows: list = []
    for _ in range(20):  # fan-out is a background job (R1.1): wait for the certificate alert to land
        async with use_tenant(tenant_id):
            rows = [n async for n in wrap(store).notifications.find(_alert_query(env_id, policy_id, ids, since))]
        if any(n["kind"] == "certificate_issued" for n in rows):
            break
        await asyncio.sleep(1.0)
    if not rows:
        raise StepFailed("No alerts were created for the synthetic change.")
    if not any(n["user_id"] == ids["owner"] for n in rows):
        raise StepFailed("The synthetic client owner got no alert.")
    leaked = [n["kind"] for n in rows if not n.get("test")]
    if leaked:
        raise StepFailed(f"{len(leaked)} alert(s) were not marked as test data ({', '.join(sorted(set(leaked)))}).")
    sent = [n["kind"] for n in rows if "sent" in (n["channels_state"].get("email"), n["channels_state"].get("push"))]
    if sent:
        raise StepFailed(f"{len(sent)} alert(s) were emailed or pushed.")
    return f"{len(rows)} alerts created, all test-only, none emailed or pushed."


async def _cleanup(http, as_, store, tenant_id, env_id, policy_id, ids, sess) -> None:
    safe = wrap(store)
    try:
        if env_id:
            async with use_tenant(tenant_id):
                card = await safe.proj_queue.find_one({"envelope_id": env_id, "resolved_at": None})
            if card:
                await http.post(f"/api/o/queue/{env_id}/reject", headers=as_("ae"), json={"reason": "Release check cleanup"})
        async with use_tenant(tenant_id):
            q = _alert_query(env_id or "-", policy_id, ids)
            await safe.notifications.update_many(q, {"$set": {"test": True}})
            for ch in ("email", "push"):
                await safe.notifications.update_many({**q, f"channels_state.{ch}": {"$in": ["pending", "held", "retry"]}},
                                                     {"$set": {f"channels_state.{ch}": "skipped:test"}})
            if env_id and not await safe.test_flags.find_one({"kind": "envelope", "ref": env_id}):  # backstop
                await safe.test_flags.insert_one({"kind": "envelope", "ref": env_id, "flagged_at": datetime.now(timezone.utc),
                                                  "reason": "release_check"})
            await safe.sessions.update_many({"session_id": {"$in": list(sess.values())}},
                                            {"$set": {"revoked_at": datetime.now(timezone.utc)}})
    except Exception as e:  # noqa: BLE001 — cleanup must not mask the run result
        log.warning("release check cleanup issue: %r", e)


async def acquire_lock(store, run_id: str) -> bool:
    # tenant-safe: system-only — one global lock row so only one release check runs at a time.
    now = datetime.now(timezone.utc)
    try:
        await store.release_check_lock.insert_one({"_id": "lock", "run_id": run_id, "started_at": now})
        return True
    except DuplicateKeyError:
        r = await store.release_check_lock.update_one({"_id": "lock", "started_at": {"$lt": now - LOCK_STALE}},
                                                      {"$set": {"run_id": run_id, "started_at": now}})
        return bool(r.modified_count)


async def release_lock(store, run_id: str) -> None:
    # tenant-safe: system-only — the global lock row.
    await store.release_check_lock.delete_one({"_id": "lock", "run_id": run_id})
