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
