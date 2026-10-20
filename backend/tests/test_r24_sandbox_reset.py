"""R2.4: burmac-sandbox reset → blank drafts; refused for every other tenant."""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.auth import sessions as sess
from app.auth.dispatch import create_session_scoped
from app.db.tenant_safe import wrap
from app.kernel import compliance_library as lib
from app.kernel import platform, sandbox, trail_mongo
from app.kernel.tenant_ctx import use_tenant
from tests.test_r23_business_details import GOOD, _txt


async def _pip(db, tid):
    async with use_tenant(tid):
        safe = wrap(db)
        u = await safe.users.find_one({"email": sandbox.STAFF[0]["email"]})
        r = await safe.user_brokerage_roles.find_one({"user_id": u["user_id"]})
    s = await create_session_scoped(db, tid, user_id=u["user_id"], brokerage_id=r["brokerage_id"], active_role="principal",
                                    mfa_verified=True, ttl=timedelta(minutes=5))
    return {"Cookie": f"{sess.COOKIE_NAME}={s.session_id}"}


@pytest.mark.asyncio
async def test_sandbox_reset_back_to_blank(app_client, mongo_db):
    _, http = app_client
    db = mongo_db
    tid = (await sandbox.ensure(db))["tenant_id"]
    pip = await _pip(db, tid)
    assert (await http.get("/api/o/compliance/business-details", headers=pip)).json()["sandbox"] is True
    assert (await http.put("/api/o/compliance/business-details", headers=pip, json=GOOD)).status_code == 200
    docs = {d["kind"]: d for d in await lib.listing(db, tid) if d.get("doc_id")}
    for k in ("GAW", "AFCA"):
        await lib.approve(db, tid, docs[k]["doc_id"], reviewer_name="Rita", confirmed=True, by="p", role="principal")
    await lib.make_live(db, tid, docs["AFCA"]["doc_id"], by="p", role="principal")
    await lib.edit_section(db, tid, docs["FSG"]["doc_id"], 0, "Edited intro wording for the sandbox test.", by="p", role="principal")
    assert {i["key"]: i["done"] for i in await platform.checklist(db, tid)}["afca"] is True

    r = await http.post("/api/o/compliance/sandbox/reset", headers=pip)
    assert r.status_code == 200
    c = r.json()
    assert c["details_cleared"] is True and c["withdrawn"] == 2 and c["created"] == 2 and c["redrafted"] == 3
    after = {d["kind"]: d for d in await lib.listing(db, tid) if d.get("doc_id")}
    assert set(after) == set(lib.KINDS) and all(d["status"] == "draft" for d in after.values())
    for d in after.values():
        assert d["blanks"]["filled"] == 0 and not d.get("overrides")
    assert after["FSG"]["blanks"]["total"] == 14 and after["FSG"]["placeholders"] == 14
    pdf, _, _ = await lib.download(db, tid, after["FSG"]["doc_id"], "pdf")
    assert b"DRAFT" in _txt(pdf)
    with pytest.raises(lib.Bad, match="blanks still to fill"):
        await lib.approve(db, tid, after["FSG"]["doc_id"], reviewer_name="Rita", confirmed=True, by="p", role="principal")
    assert {i["key"]: i["done"] for i in await platform.checklist(db, tid)}["afca"] is False
    async with use_tenant(tid):
        safe = wrap(db)
        t = await safe.system("tenants").find_one({"tenant_id": tid})
        ev = await safe.events.find_one({"type": "BusinessDetailsUpdated", "payload.reason": "sandbox_reset"})
        withdrawn = await safe.events.count_documents({"type": "ComplianceDocWithdrawn"})
        n_events = await safe.events.count_documents({})
    assert not any(t.get(k) for k in ("legal_name", "abn", "afsl", "ar_number")) and ev and withdrawn == 2
    # idempotent: a blank sandbox only gets the SandboxReset event
    c2 = (await http.post("/api/o/compliance/sandbox/reset", headers=pip)).json()
    assert c2 == {"details_cleared": False, "withdrawn": 0, "redrafted": 0, "created": 0}
    async with use_tenant(tid):
        assert await safe.events.count_documents({}) == n_events + 1
        assert await safe.events.count_documents({"type": "SandboxReset"}) == 2


@pytest.mark.parametrize("slug,row", [("burmac", {}), ("harbourline", {}), ("acme-wizard", {"kind": "brokerage"}),
                                      ("burmac-sandbox-x", {"test": True})])
@pytest.mark.asyncio
async def test_reset_refused_elsewhere(app_client, mongo_db, slug, row):
    _, http = app_client
    tid = str(uuid.uuid4())
    async with use_tenant(tid):
        await trail_mongo.create_tenant(wrap(mongo_db), slug, slug, tenant_id_override=tid)
    await mongo_db.tenants.update_one({"tenant_id": tid}, {"$set": row})  # tenant-safe: system-only
    with pytest.raises(sandbox.NotSandbox):
        await sandbox.reset(mongo_db, tid, by="x", role="principal")
    s = await create_session_scoped(mongo_db, tid, user_id="x", brokerage_id="b", active_role="principal", mfa_verified=True,
                                    ttl=timedelta(minutes=5))
    h = {"Cookie": f"{sess.COOKIE_NAME}={s.session_id}"}
    assert (await http.post("/api/o/compliance/sandbox/reset", headers=h)).status_code in (403, 404)
    assert (await http.get("/api/o/compliance/business-details", headers=h)).json().get("sandbox") in (False, None)
