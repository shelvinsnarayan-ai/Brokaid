"""R2.3 — "burmac-sandbox": a TEST tenant (test/demo-flagged) carrying the same 5 BurMac draft templates, so the owner can try
fill → review → approve with made-up details on preview. Real BurMac details are entered in production by BurMac's own staff.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pymongo.errors import DuplicateKeyError

from app.db.tenant_safe import wrap
from app.kernel import compliance_library as lib
from app.kernel import trail_mongo
from app.kernel.tenant_ctx import use_tenant
from app.seeds.seed_users_mongo import _ensure_operator_mongo

SLUG, NAME = "burmac-sandbox", "BurMac Sandbox (test)"
STAFF = [{"email": "principal@burmac-sandbox.example", "display_name": "Pip Principal", "role": "principal"},
         {"email": "operator@burmac-sandbox.example", "display_name": "Oz Operator", "role": "operator"},
         {"email": "ae@burmac-sandbox.example", "display_name": "Ari Executive", "role": "ae"}]


async def find(store) -> dict | None:
    return await store.tenants.find_one({"slug": SLUG})  # tenant-safe: system-only — tenant registry read


async def ensure(store) -> dict:
    """Idempotent: tenant (+ genesis), brokerage, three staff logins, and the 5 draft templates."""
    safe = wrap(store)
    t = await find(store)
    if not t:
        tid = str(uuid.uuid4())
        async with use_tenant(tid):
            try:
                await trail_mongo.create_tenant(safe, SLUG, NAME, tenant_id_override=tid)
            except (DuplicateKeyError, ValueError):
                pass
        t = await find(store)
    tid = t["tenant_id"]
    await safe.system("tenants").update_one({"tenant_id": tid}, {"$set": {"test": True, "demo": True, "sandbox_of": "burmac"}})
    async with use_tenant(tid):
        b = await safe.brokerages.find_one({"slug": SLUG})
        if not b:
            b = {"brokerage_id": str(uuid.uuid4()), "tenant_id": tid, "slug": SLUG, "display_name": NAME,
                 "licensee": "[test tenant — made-up details only]", "created_at": datetime.now(timezone.utc)}
            await safe.brokerages.insert_one(dict(b))
    for spec in STAFF:
        await _ensure_operator_mongo(safe, tid, b["brokerage_id"], spec)
    made = await lib.generate_drafts(store, tid, by="system", role="r23-sandbox")
    return {"tenant_id": tid, "created_drafts": [m["kind"] for m in made]}


class NotSandbox(Exception):
    pass


async def require(store, tid: str | None) -> dict:
    """Only the burmac-sandbox TEST tenant, in dev mode. Everything else (BurMac, Harbourline, real/wizard tenants) is refused."""
    from app.kernel import devmode
    t = await store.tenants.find_one({"tenant_id": tid}) if tid else None  # tenant-safe: system-only — tenant registry read
    if not (devmode.dev_mode() and t and t.get("slug") == SLUG and t.get("sandbox_of") == "burmac" and t.get("test")
            and await devmode.is_demo_tenant(store, tid)):
        raise NotSandbox()
    return t


async def reset(store, tid: str, *, by: str, role: str) -> dict:
    """R2.4: back to blank. Details → a new empty version; approved/live docs withdrawn; fresh blank DRAFT versions. Ledger-only adds."""
    from app.kernel import business_details as bd
    await require(store, tid)
    safe = wrap(store)
    prev = await bd.current(store, tid)
    cleared = any(prev["fields"].get(k) for k in bd.KEYS)
    if cleared:
        await bd.save(store, tid, {k: "" for k in bd.KEYS}, by=by, role=role, reason="sandbox_reset")
    async with use_tenant(tid):
        await safe.system("tenants").update_one({"tenant_id": tid}, {"$set": dict.fromkeys(
            ("legal_name", "trading_name", "abn", "acn", "afsl", "ar_number"))})
        rows = [r async for r in safe.compliance_library.find({"status": {"$in": ["draft", "approved", "live"]}}, {"_id": 0}).sort("created_at", 1)]
    seen, keep, archived = set(), [], []
    for r in rows:  # R2.6: one document per kind — archive any duplicate (ledgered), keep the oldest
        if r["kind"] in seen:
            async with use_tenant(tid):
                await safe.compliance_library.update_one({"doc_id": r["doc_id"]}, {"$set": {"status": "archived", "archived_at": datetime.now(timezone.utc)}})
            await lib._event(store, tid, "ComplianceDocArchived", {"doc_id": r["doc_id"], "kind": r["kind"], "version": r.get("version"),
                             "previous_status": r["status"], "reason": "duplicate_of_kind"}, f"sandbox_dedupe:{r['doc_id']}", by, role)
            archived.append(r["kind"])
            continue
        seen.add(r["kind"])
        keep.append(r)
    rows = keep
    withdrawn, redrafted = [], []
    for r in rows:
        if r["status"] in ("approved", "live"):
            async with use_tenant(tid):
                await safe.compliance_library.update_one({"doc_id": r["doc_id"]}, {"$set": {"status": "withdrawn", "withdrawn_at": datetime.now(timezone.utc)}})
                if r["kind"] == "FSG":
                    await safe.disclosure_docs.update_many({"kind": "FSG", "active": True}, {"$set": {"active": False}})
                field = lib.KINDS[r["kind"]][1]
                if field:
                    await safe.tenant_settings.update_one({}, {"$unset": {field: ""}})
            await lib._event(store, tid, "ComplianceDocWithdrawn", {"doc_id": r["doc_id"], "kind": r["kind"], "version": r["version"],
                             "previous_status": r["status"], "reason": "sandbox_reset"}, f"sandbox_withdraw:{r['doc_id']}:{r['version']}", by, role)
            withdrawn.append(r["kind"])
            async with use_tenant(tid):  # R2.6: reuse the same document — a fresh blank DRAFT version, not a second doc
                await safe.compliance_library.update_one({"doc_id": r["doc_id"]}, {"$set": {"overrides": {}}})
            await lib._new_version(store, tid, {**r, "overrides": {}}, reason="sandbox_reset", by=by, role=role)
        elif r.get("overrides") or cleared or (r.get("blanks") or {}).get("filled"):
            async with use_tenant(tid):
                await safe.compliance_library.update_one({"doc_id": r["doc_id"]}, {"$set": {"overrides": {}}})
            await lib._new_version(store, tid, {**r, "overrides": {}}, reason="sandbox_reset", by=by, role=role)
            redrafted.append(r["kind"])
    created = [m["kind"] for m in await lib.generate_drafts(store, tid, by=by, role=role)]
    counts = {"details_cleared": cleared, "withdrawn": len(withdrawn), "redrafted": len(redrafted), "created": len(created),
              **({"archived_duplicates": len(archived)} if archived else {})}
    from app.kernel.trail_dispatch import append_via_dispatch
    import uuid as _uuid
    async with use_tenant(tid):
        await append_via_dispatch(store, tid, "SandboxReset", actor={"kind": "human", "id": by, "name": role},
                                  payload={**counts, "kinds": {"withdrawn": withdrawn, "redrafted": redrafted, "created": created}},
                                  idempotency_key=f"sandbox_reset:{_uuid.uuid4()}")
    return counts
