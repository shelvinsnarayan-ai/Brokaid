"""R3.8 repair-quote summary: PDF suggestions (deterministic), blank → no card, card values on the client tracker, held reply binds the
summary to the approval (changed fields need re-approval), staff correction ledgered, operator 403, other client 404, test flag hides."""
from __future__ import annotations

import uuid

import pytest

from app.db.tenant_safe import wrap
from app.kernel import claim_threads as ct, repair_quote as rq
from app.kernel.tenant_ctx import use_tenant
from app.scripts.r36_demo_broker_quote import quote_pdf
from tests import pdf_samples as ps
from tests.test_r34_claim_threads import _staff_cookie
from tests.test_r35_claim_attachments import BLOBS, _setup, fake_storage  # noqa: F401
from tests.test_r36_broker_attachments import _sup

Q = {"repairer": "Northside Smash Repairs", "total": "2340.00", "quote_no": "Q-1042", "quote_date": "2026-09-01"}


def test_suggest_and_validate():
    assert rq.suggest(quote_pdf()) == {"total": "2340.00", "quote_no": "Q-1042", "repairer": "Northside Smash Repairs"}
    assert rq.suggest(ps.clean_pdf("Assessor report AR-77")) == {}  # no total → no suggestion
    assert rq.clean({}) is None and rq.clean({"repairer": " ", "total": ""}) is None
    for bad in ({"repairer": "X", "total": "0.50"}, {"repairer": "X", "total": "1000000.01"}, {"repairer": "X" * 121, "total": "10"},
                {"repairer": "X", "total": "12.345"}, {"repairer": "", "total": "10"}, {"repairer": "X", "total": "10", "quote_date": "1/2/26"}):
        with pytest.raises(ValueError):
            rq.clean(bad)
    c = rq.clean({"repairer": "<b>A&B</b>", "total": "2,340"}, {"total": "2340.00"})
    assert c["repairer"] == "&lt;b&gt;A&amp;B&lt;/b&gt;" and c["total"] == "2340.00" and c["source"] == {"repairer": "typed", "total": "typed"}
    assert rq.clean(Q, {"repairer": Q["repairer"], "total": "2340.00"})["source"] == {
        "repairer": "suggested", "total": "suggested", "quote_no": "typed", "quote_date": "typed"}


async def _send(http, th, cid, ae, quote=None, text="Here is the repair quote."):
    d = str(uuid.uuid4())
    f = (await _sup(http, cid, ae, quote_pdf(), "quote.pdf", d)).json()["file"]
    body = {"body": text, "idempotency_key": f"k-{d}", "file_ids": [f["file_id"]], "draft_id": d, "quotes": {f["file_id"]: quote} if quote else {}}
    return f, await http.post(f"/api/o/inbox/{th}/reply", headers=ae, json=body)


@pytest.mark.asyncio
async def test_quote_card(app_client, mongo_db):
    _, http = app_client
    tid, open_c, _closed, _f, ids, cookies, other = await _setup(mongo_db)
    cid, h, th = open_c["claim_id"], cookies["owner"], ct.thread_id_for(open_c["claim_id"])
    assert (await http.post(f"/api/c/claims/{cid}/thread", headers=h, json={"message": "Any news on the repair?"})).status_code == 200
    ae = await _staff_cookie(mongo_db, tid, ids["ae"], "ae")
    pr = await _staff_cookie(mongo_db, tid, ids["principal"], "principal")
    op = await _staff_cookie(mongo_db, tid, str(uuid.uuid4()), "operator")
    card = lambda: http.get(f"/api/c/claims/{cid}", headers=h)  # noqa: E731

    # blank fields → sent, no card; upload response carries the PDF suggestion
    f0, r = await _send(http, th, cid, ae)
    assert r.status_code == 200 and f0["quote_suggested"]["total"] == "2340.00"
    assert (await card()).json()["claim"]["quote_summary"] is None
    # invalid total → 422, nothing sent
    _, bad = await _send(http, th, cid, ae, {"repairer": "X", "total": "0"})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "quote_invalid"
    # with repairer + total → card with exact values, View quote works; other client 404
    f1, r = await _send(http, th, cid, ae, Q)
    assert r.status_code == 200, r.text
    s = (await card()).json()["claim"]["quote_summary"]
    assert s["count"] == 1 and {k: s["latest"][k] for k in Q} == Q and s["latest"]["file_id"] == f1["file_id"]
    assert (await http.get(f"/api/c/claims/{cid}/files/{f1['file_id']}", headers=h)).headers["content-type"] == "application/pdf"
    assert (await http.get(f"/api/c/claims/{cid}", headers=other["owner"])).status_code == 404
    async with use_tenant(tid):
        doc = next(d for d in (await wrap(mongo_db).claims.find_one({"claim_id": cid}))["docs"] if d["file_id"] == f1["file_id"])
    assert doc["quote"]["source"]["repairer"] == "suggested" and doc["quote"]["source"]["quote_date"] == "typed"

    # correction: operator 403, AE ok → card shows corrected values, ledgered with old/new/by
    fix = {"repairer": "Northside Smash Repairs Pty Ltd", "total": "2410.50"}
    assert (await http.patch(f"/api/o/claims/{cid}/files/{f1['file_id']}/quote", headers=op, json=fix)).status_code == 403
    assert (await http.patch(f"/api/o/claims/{cid}/files/{f1['file_id']}/quote", headers=ae, json={"repairer": "X", "total": "abc"})).status_code == 422
    assert (await http.patch(f"/api/o/claims/{cid}/files/{f1['file_id']}/quote", headers=ae, json=fix)).status_code == 200
    s = (await card()).json()["claim"]["quote_summary"]
    assert s["latest"]["repairer"] == fix["repairer"] and s["latest"]["total"] == "2410.50" and s["latest"]["quote_no"] is None
    async with use_tenant(tid):
        ev = await wrap(mongo_db).events.find_one({"type": "RepairQuoteSummaryUpdated", "payload.file_id": f1["file_id"]})
    assert ev and ev["payload"]["old"]["total"] == "2340.00" and ev["payload"]["new"]["total"] == "2410.50" and ev["payload"]["by"] == ids["ae"]

    # held reply: no card while pending; changed fields after reject need a fresh approval; card only once sent
    text = "Given your fleet, I recommend you increase the excess."
    d = str(uuid.uuid4())
    f2 = (await _sup(http, cid, ae, quote_pdf(), "quote2.pdf", d)).json()["file"]
    q2 = {**Q, "total": "1999.00"}
    assert (await http.post(f"/api/o/inbox/{th}/reply", headers=ae, json={"body": text, "idempotency_key": f"h-{d}", "file_ids": [f2["file_id"]],
                                                                            "draft_id": d, "quotes": {f2["file_id"]: q2}})).status_code == 409
    a1 = (await http.post(f"/api/o/inbox/{th}/advice-approval", headers=ae,
                          json={"body": text, "file_ids": [f2["file_id"]], "draft_id": d, "quotes": {f2["file_id"]: q2}})).json()
    assert a1["files"][0]["quote"]["total"] == "1999.00"
    assert (await card()).json()["claim"]["quote_summary"]["count"] == 1
    t = (await http.get(f"/api/o/inbox/{th}", headers=pr)).json()
    assert next(x for x in t["advice_approvals"] if x["approval_id"] == a1["approval_id"])["files"][0]["quote"]["repairer"] == Q["repairer"]
    assert (await http.post(f"/api/o/inbox/{th}/advice-approval/{a1['approval_id']}/reject", headers=pr, json={})).status_code == 200
    q3 = {**Q, "total": "2100.00"}
    a2 = (await http.post(f"/api/o/inbox/{th}/advice-approval", headers=ae,
                          json={"body": text, "file_ids": [f2["file_id"]], "draft_id": d, "quotes": {f2["file_id"]: q3}})).json()
    assert a2["approval_id"] != a1["approval_id"] and a2["files_sha256"] != a1["files_sha256"]  # summary is in the approval hash
    assert (await http.post(f"/api/o/inbox/{th}/advice-approval/{a2['approval_id']}/approve", headers=pr)).status_code == 200
    s = (await card()).json()["claim"]["quote_summary"]
    assert s["count"] == 2 and s["latest"]["file_id"] == f2["file_id"] and s["latest"]["total"] == "2100.00"

    # test-flagged reply hides its quote
    async with use_tenant(tid):
        doc = next(d for d in (await wrap(mongo_db).claims.find_one({"claim_id": cid}))["docs"] if d["file_id"] == f2["file_id"])
        await wrap(mongo_db).test_flags.insert_one({"kind": "reply", "ref": doc["reply_id"], "reason": "test"})
    s = (await card()).json()["claim"]["quote_summary"]
    assert s["count"] == 1 and s["latest"]["file_id"] == f1["file_id"]
