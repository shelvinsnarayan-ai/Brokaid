"""R3.6 broker attachments on claim-thread replies: AE/Principal upload photo + PDF (magic bytes, GPS strip, PDF script/password
checks), reply with files (advice guard/PromiseKept unchanged), one ClaimDocumentAdded per file with reply_id + label, client
sees and opens them (other client 404), staff Documents + claim pack, operator 403, closed 409, notification wording."""
from __future__ import annotations

import io
import uuid
import zipfile

import pytest

from app.db.tenant_safe import wrap
from app.kernel import claim_threads as ct, media
from app.kernel.tenant_ctx import use_tenant
from tests import media_samples as ms, pdf_samples as ps
from tests.test_r34_claim_threads import _staff_cookie
from tests.test_r35_claim_attachments import BLOBS, _setup, fake_storage  # noqa: F401


def _sup(http, claim_id, h, data, name, draft, label=None):
    return http.post(f"/api/o/claims/{claim_id}/thread/attachments", headers=h,
                     data={"msg_id": draft, **({"label": label} if label else {})}, files={"file": (name, data, "application/octet-stream")})


def _pdf_text(data: bytes) -> str:
    """Text drawn in a reportlab PDF (page streams may be Flate-compressed)."""
    import re
    import zlib
    out = [data]
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
        try:
            out.append(zlib.decompress(m[1]))
        except zlib.error:
            out.append(m[1])
    raw = b"".join(out)
    if b"ASCII85Decode" in data:
        import base64
        for m in re.finditer(rb"stream\r?\n(.*?)~>", data, re.S):
            try:
                out.append(zlib.decompress(base64.a85decode(m[1].strip(), adobe=False)))
            except Exception:  # noqa: BLE001
                pass
        raw = b"".join(out)
    return " ".join(x.decode("latin-1") for x in re.findall(rb"\((.*?)\) Tj", raw)).replace("\\267", "·").replace("\xb7", "·")


def test_pdf_checks():
    media.check_pdf(ps.clean_pdf())
    for bad in (ps.js_pdf(), ps.hex_js_pdf(), ps.hidden_js_pdf(), ps.encrypted_pdf(), ps.launch_pdf(), ps.embedded_pdf()):
        with pytest.raises(media.Rejected) as e:
            media.check_pdf(bad)
        assert e.value.code == "pdf_blocked" and str(e.value) == media.PDF_BLOCKED
    assert ct.summary([{"kind": "document", "label": "repair_quote"}], broker=True) == "a repair quote"
    assert ct.summary([{"kind": "document", "label": "assessor_report"}, {"kind": "photo", "label": "photo"},
                       {"kind": "photo", "label": "photo"}], broker=True) == "an assessor report and 2 photos"


@pytest.mark.asyncio
async def test_broker_attachments_end_to_end(app_client, mongo_db):
    _, http = app_client
    tid, open_c, closed_c, foreign_c, ids, cookies, other = await _setup(mongo_db)
    cid, h = open_c["claim_id"], cookies["owner"]
    thread_id = ct.thread_id_for(cid)
    assert (await http.post(f"/api/c/claims/{cid}/thread", headers=h, json={"message": "Has the quote come in?"})).status_code == 200
    ae = await _staff_cookie(mongo_db, tid, ids["ae"], "ae")
    draft = str(uuid.uuid4())

    quote = ps.clean_pdf()
    pdf = await _sup(http, cid, ae, quote, "quote.pdf", draft)
    assert pdf.status_code == 200, pdf.text
    pdf = pdf.json()["file"]
    ph = (await _sup(http, cid, ae, ms.photo("JPEG"), "bumper.jpg", draft)).json()["file"]
    assert (pdf["content_type"], pdf["kind"], pdf["label"]) == ("application/pdf", "document", "repair_quote")
    assert (ph["kind"], ph["label"]) == ("photo", "photo")
    async with use_tenant(tid):
        rows = {r["file_id"]: r["doc"] async for r in wrap(mongo_db).claim_uploads.find({"msg_id": draft})}
    assert media._no_gps(BLOBS[rows[ph["file_id"]]["storage_path"]])
    assert BLOBS[rows[pdf["file_id"]]["storage_path"]] == quote  # stored exactly as uploaded

    # rejections: JS PDF, encrypted PDF, EXE renamed .pdf, PDF over 20 MB, video (not for staff), bad label
    for bad in (ps.js_pdf(), ps.encrypted_pdf()):
        r = await _sup(http, cid, ae, bad, "q.pdf", draft)
        assert r.status_code == 422 and r.json()["detail"]["detail"] == media.PDF_BLOCKED
    assert (await _sup(http, cid, ae, b"MZ\x90\x00" + b"\0" * 400, "quote.pdf", draft)).status_code == 415
    r = await _sup(http, cid, ae, ps.clean_pdf() + b"\0" * (media.MAX_PDF + 1), "big.pdf", draft)
    assert r.status_code == 413 and "20 MB" in r.json()["detail"]["detail"]
    assert (await _sup(http, cid, ae, ms.video("mp4"), "v.mp4", draft)).status_code == 415
    assert (await _sup(http, cid, ae, ms.photo(), "p.jpg", draft, label="decision")).status_code == 422

    # operator: 403 on upload and on an attachment reply; closed claim: 409
    op = await _staff_cookie(mongo_db, tid, str(uuid.uuid4()), "operator")
    assert (await _sup(http, cid, op, ps.clean_pdf(), "q.pdf", str(uuid.uuid4()))).status_code == 403
    assert (await _sup(http, closed_c["claim_id"], ae, ps.clean_pdf(), "q.pdf", str(uuid.uuid4()))).status_code == 409

    # reply with both files, relabel the PDF; PromiseKept as normal; replay is idempotent
    body = {"body": "The repairer's quote and a photo of the bumper are attached.", "idempotency_key": f"r36-{draft}",
            "file_ids": [pdf["file_id"], ph["file_id"]], "draft_id": draft, "labels": {pdf["file_id"]: "repair_quote"}}
    r = await http.post(f"/api/o/inbox/{thread_id}/reply", headers=ae, json=body)
    assert r.status_code == 200, r.text
    reply_id = r.json()["reply_id"]
    assert (await http.post(f"/api/o/inbox/{thread_id}/reply", headers=ae, json=body)).json()["replayed"]
    assert (await _sup(http, cid, ae, ms.photo(), "late.jpg", draft)).status_code == 409
    async with use_tenant(tid):
        evs = [e async for e in wrap(mongo_db).events.find({"type": "ClaimDocumentAdded", "payload.reply_id": reply_id})]
        kept = await wrap(mongo_db).events.count_documents({"type": "PromiseKept", "payload.reply_id": reply_id})
        br = await wrap(mongo_db).events.find_one({"type": "BrokerReplied", "payload.reply_id": reply_id})
        notes = [n async for n in wrap(mongo_db).proj_notifications.find({"client_id": "c1", "kind": "broker_reply"})]
    assert len(evs) == 2 and {e["payload"]["label"] for e in evs} == {"repair_quote", "photo"}
    assert all(e["payload"]["uploaded_role"] == "ae" and e["payload"]["source"] == "claim_thread" for e in evs)
    assert kept == 1 and br["payload"]["attachment_summary"] == "a repair quote and a photo"
    assert any(n["subject"] == "Your broker replied with a repair quote and a photo" for n in notes)

    # client sees and opens both; other client 404
    g = (await http.get(f"/api/c/claims/{cid}/thread", headers=h)).json()
    atts = g["messages"][-1]["attachments"]
    assert [(a["kind"], a["label"]) for a in atts] == [("document", "repair_quote"), ("photo", "photo")]
    o = await http.get(f"/api/c/claims/{cid}/files/{pdf['file_id']}", headers=h)
    assert o.status_code == 200 and o.headers["content-type"] == "application/pdf" and "inline" in o.headers["content-disposition"]
    assert (await http.get(f"/api/c/claims/{cid}/files/{pdf['file_id']}", headers=other["owner"])).status_code == 404

    # attachments-only reply still counts as a reply; staff Documents + pack (PDF list and ZIP)
    d2 = str(uuid.uuid4())
    f2 = (await _sup(http, cid, ae, ps.clean_pdf("Assessor"), "assessor.pdf", d2, label="assessor_report")).json()["file"]
    r2 = await http.post(f"/api/o/inbox/{thread_id}/reply", headers=ae,
                         json={"body": "", "idempotency_key": f"r36-{d2}", "file_ids": [f2["file_id"]], "draft_id": d2})
    assert r2.status_code == 200, r2.text
    det = (await http.get(f"/api/o/claims/{cid}", headers=ae)).json()
    files = {f["file_id"]: f for f in (det.get("claim") or det)["files"]}
    assert files[pdf["file_id"]]["label"] == "repair_quote" and files[f2["file_id"]]["uploaded_role"] == "ae"
    assert (await http.post(f"/api/o/claims/{cid}/pack", headers=ae)).status_code == 200
    async with use_tenant(tid):
        c = await wrap(mongo_db).claims.find_one({"claim_id": cid})
    names = zipfile.ZipFile(io.BytesIO(BLOBS[c["pack"]["zip_path"]])).namelist()
    assert sum(n.startswith("document/") for n in names) == 2 and any(n.startswith("photo/") for n in names)
    pdf_text = _pdf_text(BLOBS[c["pack"]["pdf_path"]])
    assert "Repair quote · quote.pdf" in pdf_text and "Assessor report · assessor.pdf" in pdf_text and "Photo · bumper.jpg" in pdf_text
    assert c["stage"] == open_c["stage"]  # a label never moves the claim or records a decision

    # non-claim threads can't take attachments
    r = await http.post("/api/o/inbox/env-123/reply", headers=ae,
                        json={"body": "x", "idempotency_key": "r36-nonclaim", "file_ids": [f2["file_id"]], "draft_id": d2})
    assert r.status_code == 400
