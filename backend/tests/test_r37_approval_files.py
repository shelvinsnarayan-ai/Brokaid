"""R3.7 attach on approval: a P-tier claim reply with files is held with the files bound to the approval (sha256 + label). Nothing
reaches the client while pending. The Principal approves and one reply goes out with all files (approved_by). Tampering refuses.
Reject or withdraw keeps the drafts. A closed claim gives 409. Author = approver is refused. Text-only approvals are unchanged."""
from __future__ import annotations

import io
import uuid
import zipfile

import pytest

from app.db.tenant_safe import wrap
from app.kernel import claim_threads as ct
from app.kernel.tenant_ctx import use_tenant
from tests import media_samples as ms, pdf_samples as ps
from tests.test_r34_claim_threads import _staff_cookie
from tests.test_r35_claim_attachments import BLOBS, _setup, fake_storage  # noqa: F401
from tests.test_r36_broker_attachments import _pdf_text, _sup

P_TEXT = "Given your fleet, I recommend you increase the excess."


async def _draft(http, cid, ae):
    d = str(uuid.uuid4())
    pdf = (await _sup(http, cid, ae, ps.clean_pdf(), "quote.pdf", d)).json()["file"]
    ph = (await _sup(http, cid, ae, ms.photo(), "bumper.jpg", d)).json()["file"]
    return d, [pdf["file_id"], ph["file_id"]]


async def _held(http, th, ae, cid, text=P_TEXT):
    d, ids = await _draft(http, cid, ae)
    r = await http.post(f"/api/o/inbox/{th}/reply", headers=ae, json={"body": text, "idempotency_key": f"k-{d}", "file_ids": ids, "draft_id": d})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "PersonalAdvice"
    a = await http.post(f"/api/o/inbox/{th}/advice-approval", headers=ae,
                        json={"body": text, "file_ids": ids, "draft_id": d, "labels": {ids[0]: "assessor_report"}})
    assert a.status_code == 200, a.text
    return d, ids, a.json()["approval_id"]


@pytest.mark.asyncio
async def test_attach_on_approval(app_client, mongo_db):
    _, http = app_client
    tid, open_c, closed_c, _f, ids, cookies, other = await _setup(mongo_db)
    cid, h, th = open_c["claim_id"], cookies["owner"], ct.thread_id_for(open_c["claim_id"])
    assert (await http.post(f"/api/c/claims/{cid}/thread", headers=h, json={"message": "Should I change my excess?"})).status_code == 200
    ae = await _staff_cookie(mongo_db, tid, ids["ae"], "ae")
    pr = await _staff_cookie(mongo_db, tid, ids["principal"], "principal")
    op = await _staff_cookie(mongo_db, tid, str(uuid.uuid4()), "operator")

    d, fids, aid = await _held(http, th, ae, cid)
    # pending: invisible to the client (thread, files, notifications); held files can't go out in a normal reply or be discarded
    g = (await http.get(f"/api/c/claims/{cid}/thread", headers=h)).json()
    assert [m["who"] for m in g["messages"]] == ["client"]
    for f in fids:
        assert (await http.get(f"/api/c/claims/{cid}/files/{f}", headers=h)).status_code == 404
    async with use_tenant(tid):
        assert not await wrap(mongo_db).proj_notifications.find_one({"client_id": "c1", "kind": "broker_reply"})
        assert not await wrap(mongo_db).notifications.find_one({"kind": "broker_reply"})
    sneaky = await http.post(f"/api/o/inbox/{th}/reply", headers=ae,
                             json={"body": "Here are the files.", "idempotency_key": f"s-{d}", "file_ids": fids, "draft_id": d})
    assert sneaky.status_code == 400
    assert (await http.delete(f"/api/o/claims/{cid}/thread/attachments/{fids[0]}", headers=ae)).status_code == 404
    det = (await http.get(f"/api/o/claims/{cid}", headers=ae)).json()
    assert not {f["file_id"] for f in (det.get("claim") or det)["files"]} & set(fids)  # not claim documents while pending
    # staff see the held files; Principal can open them; operator can't approve; author can't approve own
    t = (await http.get(f"/api/o/inbox/{th}", headers=pr)).json()
    ap = next(x for x in t["advice_approvals"] if x["approval_id"] == aid)
    assert ap["status"] == "pending" and [f["label"] for f in ap["files"]] == ["assessor_report", "photo"]
    fr = await http.get(f"/api/o/inbox/{th}/advice-approval/{aid}/files/{fids[0]}", headers=pr)
    assert fr.status_code == 200 and fr.headers["content-type"] == "application/pdf"
    assert (await http.post(f"/api/o/inbox/{th}/advice-approval/{aid}/approve", headers=op)).status_code == 403

    # approve → exactly one reply with both files, approved_by, ledger, notification, pack
    r = await http.post(f"/api/o/inbox/{th}/advice-approval/{aid}/approve", headers=pr)
    assert r.status_code == 200, r.text
    reply_id = r.json()["reply_id"]
    g = (await http.get(f"/api/c/claims/{cid}/thread", headers=h)).json()
    broker = [m for m in g["messages"] if m["who"] == "broker"]
    assert len(broker) == 1 and [a["file_id"] for a in broker[0]["attachments"]] == fids and broker[0]["text"] == P_TEXT
    async with use_tenant(tid):
        evs = [e async for e in wrap(mongo_db).events.find({"type": "ClaimDocumentAdded", "payload.reply_id": reply_id})]
        req = await wrap(mongo_db).events.find_one({"type": "PersonalAdviceApprovalRequested", "payload.approval_id": aid})
        appr = await wrap(mongo_db).events.find_one({"type": "PersonalAdviceApproved", "payload.approval_id": aid})
        kept = await wrap(mongo_db).events.count_documents({"type": "PromiseKept", "payload.reply_id": reply_id})
        note = await wrap(mongo_db).proj_notifications.find_one({"client_id": "c1", "kind": "broker_reply"})
        c = await wrap(mongo_db).claims.find_one({"claim_id": cid})
    assert len(evs) == 2 and all(e["payload"]["approved_by"] == ids["principal"] for e in evs)
    assert {d_["uploaded_by"] for d_ in c["docs"] if d_["file_id"] in fids} == {ids["ae"]}
    assert len(req["payload"]["files"]) == 2 and req["payload"]["files_sha256"] == appr["payload"]["files_sha256"]
    assert kept == 1 and note["subject"] == "Your broker replied with an assessor report and a photo"
    assert (await http.post(f"/api/o/claims/{cid}/pack", headers=pr)).status_code == 200
    async with use_tenant(tid):
        c = await wrap(mongo_db).claims.find_one({"claim_id": cid})
    assert sum(n.startswith(("document/", "photo/")) for n in zipfile.ZipFile(io.BytesIO(BLOBS[c["pack"]["zip_path"]])).namelist()) == 2
    assert "Assessor report · quote.pdf" in _pdf_text(BLOBS[c["pack"]["pdf_path"]])
    # the used approval can't be reused (edited text or files need a fresh approval)
    again = await http.post(f"/api/o/inbox/{th}/reply", headers=ae, json={"body": P_TEXT + " Edited.", "idempotency_key": "k-reuse-01",
                                                                         "approval_id": aid})
    assert again.status_code == 409

    # tamper: bytes changed / label changed after the request → refused, nothing sent, drafts kept
    for tamper in ("bytes", "label"):
        d2, f2, a2 = await _held(http, th, ae, cid)
        async with use_tenant(tid):
            row = await wrap(mongo_db).claim_uploads.find_one({"file_id": f2[0]})
            if tamper == "bytes":
                BLOBS[row["doc"]["storage_path"]] = b"%PDF-1.7 tampered"
            else:
                await wrap(mongo_db).claim_uploads.update_one({"file_id": f2[0]}, {"$set": {"doc.label": "insurer_letter"}})
        r = await http.post(f"/api/o/inbox/{th}/advice-approval/{a2}/approve", headers=pr)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "approval_files_changed"
        async with use_tenant(tid):
            assert await wrap(mongo_db).claim_uploads.count_documents({"msg_id": d2, "status": "staged", "held_by": None}) == 2
    assert len([m for m in (await http.get(f"/api/c/claims/{cid}/thread", headers=h)).json()["messages"] if m["who"] == "broker"]) == 1

    # reject keeps drafts (author can then send a non-advice reply with them); withdraw by author too
    d3, f3, a3 = await _held(http, th, ae, cid)
    assert (await http.post(f"/api/o/inbox/{th}/advice-approval/{a3}/reject", headers=ae)).status_code == 403
    assert (await http.post(f"/api/o/inbox/{th}/advice-approval/{a3}/reject", headers=pr)).status_code == 200
    assert (await http.post(f"/api/o/inbox/{th}/advice-approval/{a3}/approve", headers=pr)).status_code == 404
    ok = await http.post(f"/api/o/inbox/{th}/reply", headers=ae,
                         json={"body": "The quote and photo are attached.", "idempotency_key": f"k2-{d3}", "file_ids": f3, "draft_id": d3})
    assert ok.status_code == 200, ok.text
    d4, f4, a4 = await _held(http, th, ae, cid)
    assert (await http.post(f"/api/o/inbox/{th}/advice-approval/{a4}/withdraw", headers=pr)).status_code == 403
    assert (await http.post(f"/api/o/inbox/{th}/advice-approval/{a4}/withdraw", headers=ae)).status_code == 200
    async with use_tenant(tid):
        assert await wrap(mongo_db).claim_uploads.count_documents({"msg_id": d4, "status": "staged", "held_by": None}) == 2

    # author = approver refused (a Principal who wrote the reply)
    dp, fp = await _draft(http, cid, pr)
    ap2 = (await http.post(f"/api/o/inbox/{th}/advice-approval", headers=pr, json={"body": P_TEXT, "file_ids": fp, "draft_id": dp})).json()
    r = await http.post(f"/api/o/inbox/{th}/advice-approval/{ap2['approval_id']}/approve", headers=pr)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "SeparationOfDuties"

    # claim closes while pending → 409 claim_closed, author told, drafts kept
    d5, f5, a5 = await _held(http, th, ae, cid)
    async with use_tenant(tid):
        await wrap(mongo_db).claims.update_one({"claim_id": cid}, {"$set": {"stage": "closed"}})
    r = await http.post(f"/api/o/inbox/{th}/advice-approval/{a5}/approve", headers=pr)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "claim_closed"
    async with use_tenant(tid):
        told = await wrap(mongo_db).notifications.find_one({"user_id": ids["ae"], "dedupe_key": f"advice_refused:{a5}"})
        assert told and await wrap(mongo_db).claim_uploads.count_documents({"msg_id": d5, "status": "staged", "held_by": None}) == 2
    assert (await http.post(f"/api/o/inbox/{th}/advice-approval", headers=ae, json={"body": P_TEXT, "file_ids": f5, "draft_id": d5})).status_code == 409
    # other client can never see any of it
    assert (await http.get(f"/api/c/claims/{cid}/files/{fids[0]}", headers=other["owner"])).status_code == 404
