"""R3.6 broker attachments: live-API smoke tests against preview using dev impersonation.
Covers: PDF/photo upload, pdf_blocked, encrypted, EXE→415, >20MB→413, mp4→415,
operator 403, closed claim 409, reply with files, attachments-only reply, client fetch 200 + cross-client 404,
staff Documents label/source, non-claim thread attach returns 400.
IDs created are printed at end for test-flagging."""
from __future__ import annotations

import io
import os
import struct
import uuid
import zlib

import pytest
import requests

BASE = os.environ.get("LIVE_PREVIEW_URL", "https://brokaid-demo.preview.emergentagent.com").rstrip("/")
OWNER = "owner@fictionalclient.example"
OWNER2 = "owner2@fictionalclient.example"
AE = "ae@harbourline.example"
OPERATOR = "operator@harbourline.example"
OPEN_CLAIM = "65e0039b-ac3d-4e39-bc06-dc91f04199cc"  # CLM-2026-000023, owner2
RO_CLAIM = "a3cbd499-6a45-4ca4-9b17-f8d62b45ef40"    # CLM-2026-000013 (DO NOT POST)
WITHDRAWN_REF = "CLM-2026-000024"

CREATED: dict[str, list[str]] = {"msg_ids": [], "file_ids": [], "reply_ids": []}


def _impersonate(email: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/dev/impersonate", json={"email": email}, timeout=20)
    assert r.status_code == 200, f"impersonate {email}: {r.status_code} {r.text}"
    return s


def _clean_pdf(title: str = b"Clean") -> bytes:
    if isinstance(title, str):
        title = title.encode()
    return (b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 10 10]>>endobj\n"
            b"xref\n0 4\n0000000000 65535 f \n0000000020 00000 n \n0000000060 00000 n \n0000000110 00000 n \n"
            b"trailer<</Size 4/Root 1 0 R/Info<</Title(" + title + b")>>>>\nstartxref\n160\n%%EOF\n")


def _js_pdf() -> bytes:
    base = _clean_pdf()
    return base[:50] + b"\n/JavaScript (app.alert('x'))\n" + base[50:]


def _encrypted_pdf() -> bytes:
    base = _clean_pdf()
    return base[:50] + b"\n/Encrypt 5 0 R\n" + base[50:]


def _tiny_jpeg() -> bytes:
    # Minimal valid JPEG (SOI + APP0 + DQT + SOF0 + SOS + EOI, 1x1)
    import base64
    return base64.b64decode(
        "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
        "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIy"
        "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIA"
        "AhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAn/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFAEB"
        "AAAAAAAAAAAAAAAAAAAAAP/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhEDEQA/AKpgP//Z"
    )


@pytest.fixture(scope="module")
def ae():
    return _impersonate(AE)


@pytest.fixture(scope="module")
def owner2():
    return _impersonate(OWNER2)


@pytest.fixture(scope="module")
def owner():
    return _impersonate(OWNER)


@pytest.fixture(scope="module")
def operator():
    return _impersonate(OPERATOR)


# ---------- Attachment upload validations ----------
def _upload(sess, claim_id, data, name, msg_id, label=None, ctype="application/octet-stream"):
    files = {"file": (name, data, ctype)}
    form = {"msg_id": msg_id}
    if label:
        form["label"] = label
    return sess.post(f"{BASE}/api/o/claims/{claim_id}/thread/attachments", data=form, files=files, timeout=30)


def test_pdf_upload_ok(ae):
    msg_id = str(uuid.uuid4())
    r = _upload(ae, OPEN_CLAIM, _clean_pdf(), "quote.pdf", msg_id)
    assert r.status_code == 200, r.text
    f = r.json()["file"]
    assert f["content_type"] == "application/pdf" and f["kind"] == "document" and f["label"] == "repair_quote"
    CREATED["msg_ids"].append(msg_id)
    CREATED["file_ids"].append(f["file_id"])


def test_js_pdf_blocked(ae):
    r = _upload(ae, OPEN_CLAIM, _js_pdf(), "evil.pdf", str(uuid.uuid4()))
    assert r.status_code == 422, r.text
    body = r.json()["detail"]
    assert body.get("code") == "pdf_blocked"
    assert "scripts or passwords" in body.get("detail", "")


def test_encrypted_pdf_blocked(ae):
    r = _upload(ae, OPEN_CLAIM, _encrypted_pdf(), "locked.pdf", str(uuid.uuid4()))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "pdf_blocked"


def test_exe_renamed_pdf_415(ae):
    data = b"MZ\x90\x00" + b"\x00" * 400
    r = _upload(ae, OPEN_CLAIM, data, "quote.pdf", str(uuid.uuid4()))
    assert r.status_code == 415, r.text


def test_oversize_pdf_413(ae):
    data = _clean_pdf() + b"\0" * (20 * 1024 * 1024 + 1)
    r = _upload(ae, OPEN_CLAIM, data, "big.pdf", str(uuid.uuid4()))
    assert r.status_code == 413 and "20 MB" in r.json()["detail"]["detail"]


def test_mp4_rejected_415(ae):
    # ftyp mp4 header; staff disallows video
    data = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2mp41" + b"\x00" * 200
    r = _upload(ae, OPEN_CLAIM, data, "clip.mp4", str(uuid.uuid4()))
    assert r.status_code == 415, r.text


# ---------- Role / claim-state gating ----------
def test_operator_upload_403(operator):
    r = _upload(operator, OPEN_CLAIM, _clean_pdf(), "q.pdf", str(uuid.uuid4()))
    assert r.status_code == 403, r.text


def test_withdrawn_claim_409(ae):
    # resolve withdrawn claim id
    lst = ae.get(f"{BASE}/api/o/claims?include_closed=1", timeout=20)
    assert lst.status_code == 200, lst.text
    rows = lst.json().get("claims") or lst.json().get("items") or lst.json()
    if isinstance(rows, dict):
        rows = rows.get("claims") or rows.get("items") or []
    wid = None
    for row in rows:
        if row.get("ref") == WITHDRAWN_REF or row.get("claim_ref") == WITHDRAWN_REF or row.get("claim_reference") == WITHDRAWN_REF:
            wid = row.get("claim_id") or row.get("id")
            break
    assert wid, f"could not locate {WITHDRAWN_REF} in list"
    r = _upload(ae, wid, _clean_pdf(), "q.pdf", str(uuid.uuid4()))
    assert r.status_code == 409, r.text
    body = r.json().get("detail")
    assert (isinstance(body, str) and body == "claim_closed") or (isinstance(body, dict) and body.get("code") == "claim_closed") or "closed" in str(body)


# ---------- Reply flow (text + files, then attachments-only) ----------
def test_reply_with_files_and_attachments_only(ae, owner2):
    # Upload a PDF + a photo
    msg_id = str(uuid.uuid4())
    pdf = _upload(ae, OPEN_CLAIM, _clean_pdf("QA"), "qa-quote.pdf", msg_id).json()["file"]
    ph = _upload(ae, OPEN_CLAIM, _tiny_jpeg(), "qa-bumper.jpg", msg_id, ctype="image/jpeg").json()["file"]
    CREATED["msg_ids"].append(msg_id)
    CREATED["file_ids"].extend([pdf["file_id"], ph["file_id"]])

    thread_id = f"claim-{OPEN_CLAIM}"
    body = {"body": "QA R3.6: quote + photo", "idempotency_key": f"qa-r36-{msg_id}",
            "file_ids": [pdf["file_id"], ph["file_id"]], "draft_id": msg_id,
            "labels": {pdf["file_id"]: "repair_quote"}}
    r = ae.post(f"{BASE}/api/o/inbox/{thread_id}/reply", json=body, timeout=20)
    assert r.status_code == 200, r.text
    reply_id = r.json().get("reply_id")
    assert reply_id
    CREATED["reply_ids"].append(reply_id)

    # replay idempotent
    r2 = ae.post(f"{BASE}/api/o/inbox/{thread_id}/reply", json=body, timeout=20)
    assert r2.status_code == 200 and r2.json().get("replayed") is True

    # Attachments-only reply
    msg2 = str(uuid.uuid4())
    f2 = _upload(ae, OPEN_CLAIM, _clean_pdf("Assessor"), "qa-assessor.pdf", msg2, label="assessor_report").json()["file"]
    CREATED["msg_ids"].append(msg2)
    CREATED["file_ids"].append(f2["file_id"])
    r3 = ae.post(f"{BASE}/api/o/inbox/{thread_id}/reply", json={
        "body": "", "idempotency_key": f"qa-r36-{msg2}", "file_ids": [f2["file_id"]], "draft_id": msg2}, timeout=20)
    assert r3.status_code == 200, r3.text
    CREATED["reply_ids"].append(r3.json().get("reply_id"))

    # Client (owner2) sees reply with PDF + photo attachments
    g = owner2.get(f"{BASE}/api/c/claims/{OPEN_CLAIM}/thread", timeout=20).json()
    msgs = g["messages"]
    # Find reply with pdf.file_id
    found = None
    for m in msgs:
        if any(a.get("file_id") == pdf["file_id"] for a in m.get("attachments", [])):
            found = m
            break
    assert found, "client did not see broker reply with the quote"
    kinds = {(a["kind"], a.get("label")) for a in found["attachments"]}
    assert ("document", "repair_quote") in kinds
    assert any(a["kind"] == "photo" for a in found["attachments"])

    # Client PDF Open link returns application/pdf inline
    o = owner2.get(f"{BASE}/api/c/claims/{OPEN_CLAIM}/files/{pdf['file_id']}", timeout=20)
    assert o.status_code == 200
    assert o.headers.get("content-type", "").startswith("application/pdf")
    assert "inline" in o.headers.get("content-disposition", "")


def test_cross_client_pdf_404(ae, owner):
    # owner (demo client, NOT owner2) must not be able to fetch owner2's uploaded PDF
    assert CREATED["file_ids"], "no uploaded file ids"
    pdf_fid = CREATED["file_ids"][0]
    r = owner.get(f"{BASE}/api/c/claims/{OPEN_CLAIM}/files/{pdf_fid}", timeout=20)
    assert r.status_code == 404, r.text


# ---------- Staff Documents visibility ----------
def test_staff_documents_show_label_and_source(ae):
    r = ae.get(f"{BASE}/api/o/claims/{OPEN_CLAIM}", timeout=20)
    assert r.status_code == 200, r.text
    det = r.json()
    claim = det.get("claim") or det
    files = claim.get("files") or []
    file_ids = CREATED["file_ids"]
    seen = [f for f in files if f.get("file_id") in file_ids]
    assert seen, "no QA-uploaded files on staff claim detail"
    for f in seen:
        assert f.get("uploaded_role") in ("ae", "principal")
        assert f.get("source") in ("claim_thread", "messages")


# ---------- Non-claim thread cannot take files ----------
def test_non_claim_thread_rejects_attachments(ae):
    msg_id = str(uuid.uuid4())
    f = _upload(ae, OPEN_CLAIM, _clean_pdf(), "stray.pdf", msg_id).json()["file"]
    CREATED["msg_ids"].append(msg_id)
    CREATED["file_ids"].append(f["file_id"])
    r = ae.post(f"{BASE}/api/o/inbox/env-123/reply", json={
        "body": "x", "idempotency_key": f"qa-r36-nonclaim-{msg_id}",
        "file_ids": [f["file_id"]], "draft_id": msg_id}, timeout=20)
    assert r.status_code in (400, 404, 422), r.text


# ---------- Read-only: demo client sees seeded broker quote ----------
def test_demo_client_sees_seeded_quote(owner):
    r = owner.get(f"{BASE}/api/c/claims/{RO_CLAIM}/thread", timeout=20)
    assert r.status_code == 200, r.text
    msgs = r.json()["messages"]
    hit = None
    for m in msgs:
        for a in m.get("attachments", []):
            if (a.get("name") or a.get("filename") or "").startswith("Northside-Smash-Repairs-Quote"):
                hit = (m, a)
                break
        if hit:
            break
    assert hit, "seeded broker quote not visible on CLM-2026-000013"
    m, a = hit
    assert a["kind"] == "document" and a.get("label") == "repair_quote"
    o = owner.get(f"{BASE}/api/c/claims/{RO_CLAIM}/files/{a['file_id']}", timeout=20)
    assert o.status_code == 200 and o.headers["content-type"].startswith("application/pdf")


def test_print_created_ids():
    print("CREATED_FOR_FLAG=", CREATED)
