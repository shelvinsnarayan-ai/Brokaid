"""R1.9 — platform administration: a platform_admin role (bootstrap env only) that sets up brokerages and never sees client data.

platform_admin users live in a dedicated "platform" system tenant with no clients. The role is re-checked against
PLATFORM_ADMIN_EMAILS on every request, so removing an email from the env revokes it.
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone

from fastapi import Depends, HTTPException

from app.db.tenant_safe import wrap
from app.kernel import auth_flows as af
from app.kernel import holidays, trail_mongo
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch
from app.middleware.session import require_session

PLATFORM_SLUG = "platform"
STATUSES = ("setup", "live", "suspended")
STATES = ("NSW", "VIC", "QLD", "SA", "WA", "TAS", "ACT", "NT")
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{2,30}$")


def admin_emails() -> set[str]:
    return {e.strip().lower() for e in (os.environ.get("PLATFORM_ADMIN_EMAILS") or "").split(",") if e.strip()}


def abn_valid(abn: str) -> bool:
    d = re.sub(r"\D", "", abn or "")
    if len(d) != 11:
        return False
    n = [int(x) for x in d]
    n[0] -= 1
    return sum(a * w for a, w in zip(n, (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19))) % 89 == 0


async def platform_tenant(store) -> dict | None:
    # tenant-safe: system-only — the platform tenant row
    return await store.tenants.find_one({"slug": PLATFORM_SLUG}, {"_id": 0})


async def bootstrap(store, send=None) -> str:
    """Idempotent: create the platform tenant and one platform_admin invite per env email that has no account yet."""
    emails = admin_emails()
    if not emails:
        return "skipped:not_configured"
    t = await platform_tenant(store)
    if t is None:
        tid = str(uuid.uuid4())
        async with use_tenant(tid):
            await trail_mongo.create_tenant(wrap(store), slug=PLATFORM_SLUG, display_name="brokAId platform", tenant_id_override=tid)
        await store.tenants.update_one({"tenant_id": tid}, {"$set": {"kind": "platform", "status": "live"}})  # tenant-safe: system-only
    else:
        tid = t["tenant_id"]
    await _ensure_brokerage(store, tid, PLATFORM_SLUG, "brokAId platform")
    issued = 0
    for email in sorted(emails):
        async with use_tenant(tid):
            if await wrap(store).users.find_one({"email": email}):
                continue
        open_inv = await wrap(store).system("auth_tokens").find_one(
            {"kind": "invite", "tenant_id": tid, "email": email, "used_at": None, "revoked_at": None, "expires_at": {"$gt": af.now()}})
        if open_inv:
            continue
        token, doc = await af.create_invite(store, tenant_id=tid, email=email, name="", role="platform_admin", client_id=None,
                                            created_by="bootstrap", bootstrap=True)
        await af.send_auth_email(store, tid, kind="invite", to=email, name="", link=af.invite_url(token), role="Platform admin", send=send)
        issued += 1
    return f"issued:{issued}"


async def _ensure_brokerage(store, tid: str, slug: str, name: str) -> None:
    async with use_tenant(tid):
        if not await wrap(store).brokerages.find_one({}):
            await wrap(store).brokerages.insert_one({"brokerage_id": str(uuid.uuid4()), "slug": slug, "display_name": name})


async def require_platform_admin(session=Depends(require_session)):
    from app.db import mongo as mongo_mod
    store = mongo_mod.get_db_sync()
    if not session.mfa_verified or session.active_role != "platform_admin":
        raise HTTPException(status_code=403, detail="platform_admin_only")
    t = await platform_tenant(store)
    async with use_tenant(session.tenant_id):
        u = await wrap(store).users.find_one({"user_id": session.user_id}) or {}
    if not t or t["tenant_id"] != session.tenant_id or (u.get("email") or "").lower() not in admin_emails():
        raise HTTPException(status_code=403, detail="platform_admin_only")
    return session


async def _event(store, tid, typ, payload, by):
    await append_via_dispatch(store, tid, typ, actor={"kind": "human", "id": by, "name": "platform_admin"}, payload=payload,
                              idempotency_key=f"{typ}:{tid}:{uuid.uuid4()}")


async def create(store, *, body: dict, by: str, send=None) -> dict:
    slug = (body.get("slug") or "").strip().lower()
    if not SLUG_RE.match(slug) or slug in (PLATFORM_SLUG, "harbourline"):
        raise HTTPException(status_code=422, detail="Slug: 3–31 lowercase letters, digits or dashes, starting with a letter.")
    if await store.tenants.find_one({"slug": slug}):  # tenant-safe: system-only — slug uniqueness
        raise HTTPException(status_code=409, detail="That slug is taken.")
    if not abn_valid(body.get("abn") or ""):
        raise HTTPException(status_code=422, detail="That ABN fails the checksum.")
    state = (body.get("state") or "").upper()
    if state not in STATES:
        raise HTTPException(status_code=422, detail="Choose the state for public holidays.")
    legal = (body.get("legal_name") or "").strip()
    licence = (body.get("afsl") or "").strip()
    email = (body.get("operator_email") or "").strip().lower()
    if not legal or not licence or "@" not in email:
        raise HTTPException(status_code=422, detail="Legal name, AFSL/AR number and the first Operator's email are required.")
    tid = str(uuid.uuid4())
    async with use_tenant(tid):
        await trail_mongo.create_tenant(wrap(store), slug=slug, display_name=body.get("display_name") or legal, tenant_id_override=tid)
    now = datetime.now(timezone.utc)
    await store.tenants.update_one({"tenant_id": tid}, {"$set": {  # tenant-safe: system-only — the new tenant's own row
        "legal_name": legal, "abn": re.sub(r"\D", "", body["abn"]), "afsl": licence, "timezone": body.get("timezone") or "Australia/Sydney",
        "status": "setup", "kind": "brokerage", "test": bool(body.get("test")), "created_by_platform": by, "created_at": now,
        "email_settings": {"sender_name": (body.get("sender_name") or legal)[:80], "reply_to": (body.get("reply_to") or "").strip() or None}}})
    await _ensure_brokerage(store, tid, slug, legal)
    async with use_tenant(tid):
        safe = wrap(store)
        await safe.tenant_settings.update_one({}, {"$set": {"holiday_state": state, "quoting_mode": "manual_only"}}, upsert=True)
    await _event(store, tid, "TenantCreated", {"slug": slug, "legal_name": legal, "abn": re.sub(r"\D", "", body["abn"]), "afsl": licence,
                                               "state": state, "quoting_mode": "manual_only", "status": "setup"}, by)
    token, doc = await af.create_invite(store, tenant_id=tid, email=email, name=body.get("operator_name") or "", role="operator",
                                        client_id=None, created_by=by, bootstrap=True)
    status = await af.send_auth_email(store, tid, kind="invite", to=email, name=doc["name"], link=af.invite_url(token), role="Operator",
                                      send=send)
    await _event(store, tid, "InviteCreated", {"invite_id": doc["token_id"], "role": "operator", "by_platform": True,
                                               "email_status": status.split(":")[0]}, by)
    return {"tenant_id": tid, "slug": slug, "invite_email_status": status.split(":")[0]}


async def checklist(store, tid: str) -> list[dict]:
    t = await store.tenants.find_one({"tenant_id": tid}) or {}  # tenant-safe: system-only — one tenant row by id
    hs = await holidays.tenant_settings(store, tid)
    async with use_tenant(tid):
        safe = wrap(store)
        pay = await safe.payment_settings.find_one({}) or {}
        fsg = await safe.disclosure_docs.find_one({"kind": "FSG", "active": True})
        ops = await safe.user_brokerage_roles.count_documents({"role": "operator", "active": True})
        aes = await safe.user_brokerage_roles.count_documents({"role": "ae", "active": True})
        principals = await safe.user_brokerage_roles.count_documents({"role": "principal", "active": True})
        clients = await safe.clients.count_documents({})
        unassigned = await safe.clients.count_documents({"$or": [{"assigned_ae_user_id": None}, {"assigned_ae_user_id": {"$exists": False}}]})
        profile = await safe.import_profiles.find_one({})
    from app.kernel import compliance_library
    live = await compliance_library.live_kinds(store, tid)  # R2.0: only Principal-approved, live documents count
    items = [
        ("operator", "First Operator signed in", ops > 0),
        ("principal", "A Principal is set up", principals > 0),
        ("fsg", "FSG approved by a Principal and live", "FSG" in live and bool(fsg)),
        ("gaw", "General advice warning approved and live", "GAW" in live),
        ("afca", "AFCA / complaints text approved and live", "AFCA" in live),
        ("privacy", "Privacy policy approved and live", "PRIVACY_POLICY" in live),
        ("collection", "Collection notice approved and live", "COLLECTION_NOTICE" in live),
        ("payment", "Payment settings (account name, BSB, account number)", all(pay.get(k) for k in ("account_name", "bsb", "account_number"))),
        ("email", "Email branding (sender name, reply-to)", bool((t.get("email_settings") or {}).get("reply_to"))),
        ("iadviser", "iAdviser import profile", bool(profile)),
        ("aes", "AEs set up and every client assigned", aes > 0 and (clients == 0 or unassigned == 0)),
        ("holidays", "Public-holiday state", hs["holiday_state"] in STATES),
        ("quoting", "Quoting mode is manual only (real tenant)", hs["quoting_mode"] == "manual_only" or bool(t.get("test"))),
    ]
    return [{"key": k, "label": label, "done": bool(done)} for k, label, done in items]


async def set_status(store, tid: str, status: str, reason: str, by: str, force: bool = False) -> dict:
    if status not in STATUSES:
        raise HTTPException(status_code=422, detail="Status is setup, live or suspended.")
    t = await store.tenants.find_one({"tenant_id": tid})  # tenant-safe: system-only
    if not t or t.get("kind") == "platform":
        raise HTTPException(status_code=404, detail="tenant_not_found")
    if status == "live" and not force:
        missing = [i["label"] for i in await checklist(store, tid) if not i["done"]]
        if missing:
            raise HTTPException(status_code=409, detail={"code": "GoLiveIncomplete", "missing": missing})
    await store.tenants.update_one({"tenant_id": tid}, {"$set": {"status": status, "status_reason": reason[:300]}})  # tenant-safe: system-only
    await _event(store, tid, "TenantStatusChanged", {"from": t.get("status") or "live", "to": status, "reason": reason[:300],
                                                     "forced": force}, by)
    if status == "suspended":
        await wrap(store).system("sessions").update_many({"tenant_id": tid, "revoked_at": None}, {"$set": {"revoked_at": af.now()}})
    return {"tenant_id": tid, "status": status}


async def is_suspended(store, tid: str) -> bool:
    t = await store.tenants.find_one({"tenant_id": tid}, {"status": 1})  # tenant-safe: system-only
    return bool(t and t.get("status") == "suspended")


async def tenants(store) -> list[dict]:
    out = []
    async for t in store.tenants.find({"kind": {"$ne": "platform"}}, {"_id": 0}):  # tenant-safe: system-only — platform list
        items = await checklist(store, t["tenant_id"])
        out.append({"tenant_id": t["tenant_id"], "slug": t["slug"], "name": t.get("legal_name") or t.get("display_name"),
                    "status": t.get("status") or "live", "test": bool(t.get("test")), "abn": t.get("abn"), "afsl": t.get("afsl"),
                    "missing": [i["label"] for i in items if not i["done"]], "checklist": items})
    return out
