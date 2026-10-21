"""R3.4 claim message threads: list/read/post, closed read-only, cross-client 404, test data, authority, staff Inbox + claim panel, unread."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.auth.dispatch import create_session_scoped
from app.auth.sessions import COOKIE_NAME
from app.db.tenant_safe import wrap
from app.kernel import claim_threads as ct, inbox
from app.kernel.tenant_ctx import use_tenant
from tests.test_r16_renewals import _people, _tenant


def _claim(cid, ref, stage="triage", test=False):
    now = datetime.now(timezone.utc)
    return {"claim_id": str(uuid.uuid4()), "client_id": cid, "claim_reference": ref, "claim_class": "motor", "subtype": "accident",
            "claim_type": "motor_accident", "stage": stage, "status": stage, "lodged_at": now - timedelta(days=2), "opened_at": now,
            "ack_due": now + timedelta(days=1), "ack_at": now, "stage_since": now, "incident": {"rego": "ABC123"}, "history": [],
            "product": "Motor — commercial", "test": test, "flags": [], "docs": [], "info_requests": []}


async def _staff_cookie(db, tid, uid, role):
    s = await create_session_scoped(db, tid, user_id=uid, client_id=None, active_role=role, mfa_verified=True, ttl=timedelta(minutes=30))
    return {"Cookie": f"{COOKIE_NAME}={s.session_id}"}


@pytest.mark.asyncio
async def test_claim_threads_end_to_end(app_client, mongo_db):
    _, http = app_client
    db = mongo_db
    tid = await _tenant(db, f"r34-{uuid.uuid4().hex[:4]}")
    open_c, closed_c, foreign_c, test_c = _claim("c1", "CLM-1"), _claim("c1", "CLM-2", "closed"), _claim("c2", "CLM-3"), _claim("c1", "CLM-4", test=True)
    async with use_tenant(tid):
        s = wrap(db)
        for cid in ("c1", "c2"):
            await s.clients.insert_one({"client_id": cid, "display_name": f"Client {cid}"})
        await s.policies.insert_one({"policy_id": "p1", "client_id": "c1", "status": "managed", "product": "Motor",
                                     "schedule": {"vehicles": [{"rego": "ABC123", "make": "Toyota", "model": "Hilux"}]}})
        for c in (open_c, closed_c, foreign_c, test_c):
            await s.claims.insert_one(dict(c))
    ids, cookies = await _people(db, tid, "c1")
    _, other = await _people(db, tid, "c2")
    h = cookies["owner"]

    rows = (await http.get("/api/c/claim-threads", headers=h)).json()["threads"]
    assert [r["claim_reference"] for r in rows] == ["CLM-1"]  # open claim with no messages listed; closed w/o thread, foreign, test hidden
    assert rows[0]["label"] == "Motor claim · Toyota Hilux ABC123" and rows[0]["thread_id"] is None and rows[0]["unread"] == 0

    r = await http.post(f"/api/c/claims/{open_c['claim_id']}/thread", headers=h, json={"message": "Is the assessor booked?"})
    assert r.status_code == 200, r.text
    thread_id = ct.thread_id_for(open_c["claim_id"])
    g = (await http.get(f"/api/c/claims/{open_c['claim_id']}/thread", headers=h)).json()
    assert g["exists"] and [m["who"] for m in g["messages"]] == ["client"] and g["can_post"] and g["reply_by_text"]
    assert (await http.post(f"/api/c/claims/{open_c['claim_id']}/thread", headers=h, json={"message": "  "})).status_code == 422
    assert (await http.post(f"/api/c/claims/{open_c['claim_id']}/thread", headers=h, json={"message": "x" * 4001})).status_code == 422
    assert (await http.post(f"/api/c/claims/{open_c['claim_id']}/thread", headers=h, json={"message": {"$ne": 1}})).status_code in (400, 422)

    # main Ask thread does not show claim threads; one ledgered ClientQuestionAsked with claim_id; no AI auto-reply
    assert all(t.get("thread_id") != thread_id for t in (await http.get("/api/c/ask/thread", headers=h)).json().get("threads", []))
    async with use_tenant(tid):
        q = [e async for e in wrap(db).events.find({"type": "ClientQuestionAsked", "payload.envelope_id": thread_id})]
        assert len(q) == 1 and q[0]["payload"]["claim_id"] == open_c["claim_id"]
        assert await wrap(db).events.count_documents({"type": "BrokerReplied", "payload.thread_id": thread_id}) == 0

    # staff: Inbox row tagged Claim; claim page panel; AE can reply, operator cannot
    ae = await _staff_cookie(db, tid, ids["ae"], "ae")
    inbox_rows = (await http.get("/api/o/inbox", headers=ae)).json()["threads"]
    row = next(t for t in inbox_rows if t["thread_id"] == thread_id)
    assert row["claim"]["claim_id"] == open_c["claim_id"] and row["claim"]["label"].startswith("CLM-1 · Motor claim")
    panel = (await http.get(f"/api/o/claims/{open_c['claim_id']}/thread", headers=ae)).json()
    assert panel["exists"] and panel["can_reply"] and panel["conversation"][0]["text"] == "Is the assessor booked?"
    rep = await http.post(f"/api/o/inbox/{thread_id}/reply", headers=ae, json={"body": "Yes, Thursday 10am.", "idempotency_key": "r34-test-1"})
    assert rep.status_code == 200, rep.text
    op = SimpleNamespace(tenant_id=tid, user_id=ids["ae"], active_role="operator", client_id=None)
    with pytest.raises(HTTPException) as e:
        await inbox.reply(db, op, thread_id, "no", "r34-op-1")
    assert e.value.status_code == 403

    # client: unread reply + highlight ids, badge clears on view; second round gets a fresh promise that is kept
    rows = (await http.get("/api/c/claim-threads", headers=h)).json()["threads"]
    assert rows[0]["unread"] == 1
    g = (await http.get(f"/api/c/claims/{open_c['claim_id']}/thread", headers=h)).json()
    assert len(g["unread_reply_ids"]) == 1 and g["messages"][-1]["who"] == "broker"
    unread = (await http.get("/api/c/ask/unread", headers=h)).json()
    assert unread["count"] == 1 and unread["threads"][0]["claim_id"] == open_c["claim_id"]
    assert (await http.post("/api/c/ask/seen", headers=h, json={"thread_id": thread_id})).status_code == 200
    assert (await http.get("/api/c/claim-threads", headers=h)).json()["threads"][0]["unread"] == 0
    await http.post(f"/api/c/claims/{open_c['claim_id']}/thread", headers=h, json={"message": "Thanks — one more thing."})
    t = await ct.thread_for(db, tid, "c1", open_c["claim_id"])
    assert not t["answered"] and t["reply_by"] and t["kept"] is None
    await http.post(f"/api/o/inbox/{thread_id}/reply", headers=ae, json={"body": "Sure, go ahead.", "idempotency_key": "r34-test-2"})
    async with use_tenant(tid):
        assert await wrap(db).events.count_documents({"type": "PromiseKept", "payload.thread_id": thread_id}) == 2

    # closed claim: read-only, API refuses with a clear error
    assert (await http.get(f"/api/c/claims/{closed_c['claim_id']}/thread", headers=h)).json()["read_only"] is True
    r = await http.post(f"/api/c/claims/{closed_c['claim_id']}/thread", headers=h, json={"message": "hello"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "claim_closed"

    # cross-client and test data: 404 on read and post; list for c2 has none of c1's
    for path in (f"/api/c/claims/{open_c['claim_id']}/thread",):
        assert (await http.get(path, headers=other["owner"])).status_code == 404
        assert (await http.post(path, headers=other["owner"], json={"message": "hi"})).status_code == 404
    assert all(r["claim_id"] != open_c["claim_id"] for r in (await http.get("/api/c/claim-threads", headers=other["owner"])).json()["threads"])
    assert (await http.post("/api/c/ask/seen", headers=other["owner"], json={"thread_id": thread_id})).status_code == 404
    assert (await http.get(f"/api/c/claims/{test_c['claim_id']}/thread", headers=h)).status_code == 404

    # bookkeeper: can read; posting follows lodgement's can_lodge_claim switch
    assert (await http.get(f"/api/c/claims/{open_c['claim_id']}/thread", headers=cookies["bookkeeper"])).status_code == 200
    async with use_tenant(tid):
        await wrap(db).memberships.update_one({"client_id": "c1", "role": "bookkeeper"}, {"$set": {"authority.can_lodge_claim": False}})
    g = (await http.get(f"/api/c/claims/{open_c['claim_id']}/thread", headers=cookies["bookkeeper"])).json()
    assert g["can_post"] is False
    assert (await http.post(f"/api/c/claims/{open_c['claim_id']}/thread", headers=cookies["bookkeeper"],
                            json={"message": "hi"})).status_code == 403


def test_openapi_lists_new_endpoints():
    from app.main import app
    paths = app.openapi()["paths"]
    for p in ("/api/c/claim-threads", "/api/c/claims/{claim_id}/thread", "/api/o/claims/{claim_id}/thread"):
        assert p in paths, p
