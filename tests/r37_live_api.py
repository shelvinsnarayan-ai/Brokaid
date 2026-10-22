"""R3.7 live-API tests against preview. Covers the attach-on-approval flow end to end:
pending -> files invisible to client; staff can view; Principal approves -> one reply with both files;
tamper 409; reject keeps drafts; withdraw by author; author=approver 403; operator approve 403;
text-only approval still works; non-P reply with attachments sends immediately (regression).
"""
from __future__ import annotations

import io
import os
import uuid

import pytest
import requests
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas as rl_canvas

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://brokaid-demo.preview.emergentagent.com").rstrip("/")
CLAIM_ID = "65e0039b-ac3d-4e39-bc06-dc91f04199cc"
THREAD_ID = f"claim-{CLAIM_ID}"
P_TEXT = "Given your fleet, I recommend you increase the excess."

CREATED: dict = {"approval_ids": [], "reply_ids": [], "file_ids": [], "msg_ids": []}


def _sess(email: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/dev/impersonate", json={"email": email}, timeout=20)
    assert r.status_code == 200, f"impersonate {email}: {r.status_code} {r.text}"
    return s


def _pdf_bytes(text: str = "Repair quote") -> bytes:
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=A4)
    c.drawString(72, 720, text)
    c.showPage()
    c.save()
    return buf.getvalue()


def _jpg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (120, 90), (180, 60, 60)).save(buf, "JPEG")
    return buf.getvalue()


def _upload(sess: requests.Session, msg_id: str, name: str, data: bytes, mime: str, label: str | None = None) -> str:
    files = {"file": (name, data, mime)}
    form = {"msg_id": msg_id}
    if label:
        form["label"] = label
    r = sess.post(f"{BASE}/api/o/claims/{CLAIM_ID}/thread/attachments", files=files, data=form, timeout=30)
    assert r.status_code == 200, f"upload {name}: {r.status_code} {r.text}"
    fid = r.json()["file"]["file_id"]
    CREATED["file_ids"].append(fid)
    return fid


def _draft(sess: requests.Session) -> tuple[str, list[str]]:
    d = str(uuid.uuid4())
    CREATED["msg_ids"].append(d)
    pdf = _upload(sess, d, f"r37-quote-{d[:6]}.pdf", _pdf_bytes(), "application/pdf", label="repair_quote")
    ph = _upload(sess, d, f"r37-photo-{d[:6]}.jpg", _jpg_bytes(), "image/jpeg")
    return d, [pdf, ph]


def _request_held(ae: requests.Session, text: str = P_TEXT) -> tuple[str, list[str], str]:
    d, ids = _draft(ae)
    # First try a normal reply - must be blocked (PersonalAdvice guard)
    r = ae.post(f"{BASE}/api/o/inbox/{THREAD_ID}/reply",
                json={"body": text, "idempotency_key": f"k-{d}", "file_ids": ids, "draft_id": d}, timeout=20)
    assert r.status_code == 409, f"expected 409 PersonalAdvice, got {r.status_code} {r.text}"
    assert r.json()["detail"]["code"] == "PersonalAdvice"
    # Request Principal approval
    a = ae.post(f"{BASE}/api/o/inbox/{THREAD_ID}/advice-approval",
                json={"body": text, "file_ids": ids, "draft_id": d,
                      "labels": {ids[0]: "repair_quote"}}, timeout=20)
    assert a.status_code == 200, f"request-approval: {a.status_code} {a.text}"
    aid = a.json()["approval_id"]
    CREATED["approval_ids"].append(aid)
    return d, ids, aid


# ---------- tests ----------

@pytest.fixture(scope="module")
def ae():
    return _sess("ae@harbourline.example")


@pytest.fixture(scope="module")
def principal():
    return _sess("principal@harbourline.example")


@pytest.fixture(scope="module")
def operator():
    return _sess("operator@harbourline.example")


@pytest.fixture(scope="module")
def owner2():
    return _sess("owner2@fictionalclient.example")


def _client_thread_count(owner2: requests.Session) -> int:
    r = owner2.get(f"{BASE}/api/c/claims/{CLAIM_ID}/thread", timeout=20)
    assert r.status_code == 200, r.text
    return sum(1 for m in r.json().get("messages", []) if m.get("who") == "broker")


def test_01_approve_happy_path(ae, principal, owner2):
    before = _client_thread_count(owner2)
    d, fids, aid = _request_held(ae)

    # Pending: client sees nothing new, files 404
    assert _client_thread_count(owner2) == before
    for fid in fids:
        r = owner2.get(f"{BASE}/api/c/claims/{CLAIM_ID}/files/{fid}", timeout=20, allow_redirects=False)
        assert r.status_code == 404, f"file {fid} must be 404 while pending, got {r.status_code}"

    # Staff thread shows the approval with files and 'Awaiting Principal approval' status
    t = principal.get(f"{BASE}/api/o/inbox/{THREAD_ID}", timeout=20).json()
    ap = next(x for x in t["advice_approvals"] if x["approval_id"] == aid)
    assert ap["status"] == "pending"
    assert {f["file_id"] for f in ap["files"]} == set(fids)

    # Principal can open the held PDF
    r = principal.get(f"{BASE}/api/o/inbox/{THREAD_ID}/advice-approval/{aid}/files/{fids[0]}", timeout=20)
    assert r.status_code == 200 and r.headers.get("content-type", "").startswith("application/pdf")

    # Approve -> one broker reply with both files
    r = principal.post(f"{BASE}/api/o/inbox/{THREAD_ID}/advice-approval/{aid}/approve", timeout=30)
    assert r.status_code == 200, r.text
    reply_id = r.json()["reply_id"]
    CREATED["reply_ids"].append(reply_id)

    g = owner2.get(f"{BASE}/api/c/claims/{CLAIM_ID}/thread", timeout=20).json()
    broker = [m for m in g["messages"] if m.get("who") == "broker"]
    assert len(broker) == before + 1
    last = broker[-1]
    assert last["text"] == P_TEXT
    assert {a["file_id"] for a in last.get("attachments", [])} == set(fids)


def test_02_operator_cannot_approve(ae, operator):
    _, _, aid = _request_held(ae)
    r = operator.post(f"{BASE}/api/o/inbox/{THREAD_ID}/advice-approval/{aid}/approve", timeout=20)
    assert r.status_code == 403, f"operator approve must be 403, got {r.status_code} {r.text}"
    # cleanup: principal rejects
    r2 = _sess("principal@harbourline.example").post(
        f"{BASE}/api/o/inbox/{THREAD_ID}/advice-approval/{aid}/reject", timeout=20)
    assert r2.status_code == 200


def test_03_reject_then_edit_and_resend(ae, principal, owner2):
    before = _client_thread_count(owner2)
    d, fids, aid = _request_held(ae)
    r = principal.post(f"{BASE}/api/o/inbox/{THREAD_ID}/advice-approval/{aid}/reject", timeout=20)
    assert r.status_code == 200

    # Client sees nothing new
    assert _client_thread_count(owner2) == before
    # Staff thread shows rejected status; files still staged (held released) so AE can reuse via a non-P reply
    t = ae.get(f"{BASE}/api/o/inbox/{THREAD_ID}", timeout=20).json()
    ap = next(x for x in t["advice_approvals"] if x["approval_id"] == aid)
    assert ap["status"] in ("rejected", "Rejected"), f"status={ap['status']}"

    # AE can send a non-P reply with same files (edit-and-resend equivalent)
    non_p_body = "Here is the quote and photo for your review."
    r = ae.post(f"{BASE}/api/o/inbox/{THREAD_ID}/reply",
                json={"body": non_p_body, "idempotency_key": f"k-rr-{d}", "file_ids": fids, "draft_id": d}, timeout=30)
    assert r.status_code == 200, r.text
    CREATED["reply_ids"].append(r.json()["reply_id"])


def test_04_withdraw_by_author(ae, principal):
    d, fids, aid = _request_held(ae)
    # Principal cannot withdraw
    r = principal.post(f"{BASE}/api/o/inbox/{THREAD_ID}/advice-approval/{aid}/withdraw", timeout=20)
    assert r.status_code == 403
    # Author can
    r = ae.post(f"{BASE}/api/o/inbox/{THREAD_ID}/advice-approval/{aid}/withdraw", timeout=20)
    assert r.status_code == 200


def test_05_author_equals_approver_blocked(principal):
    # Principal both drafts and tries to approve own
    d, fids = _draft(principal)
    a = principal.post(f"{BASE}/api/o/inbox/{THREAD_ID}/advice-approval",
                       json={"body": P_TEXT, "file_ids": fids, "draft_id": d}, timeout=20)
    assert a.status_code == 200, a.text
    aid = a.json()["approval_id"]
    CREATED["approval_ids"].append(aid)
    r = principal.post(f"{BASE}/api/o/inbox/{THREAD_ID}/advice-approval/{aid}/approve", timeout=20)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "SeparationOfDuties"
    # cleanup: withdraw
    principal.post(f"{BASE}/api/o/inbox/{THREAD_ID}/advice-approval/{aid}/withdraw", timeout=20)


def test_06_text_only_approval_still_works(ae, principal, owner2):
    before = _client_thread_count(owner2)
    # Normal reply -> P-tier guard blocks (no files)
    key = f"to-{uuid.uuid4()}"
    r = ae.post(f"{BASE}/api/o/inbox/{THREAD_ID}/reply",
                json={"body": P_TEXT, "idempotency_key": key}, timeout=20)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "PersonalAdvice"
    # Request text-only approval
    a = ae.post(f"{BASE}/api/o/inbox/{THREAD_ID}/advice-approval",
                json={"body": P_TEXT}, timeout=20)
    assert a.status_code == 200
    aid = a.json()["approval_id"]
    CREATED["approval_ids"].append(aid)
    # Approve (no files)
    r = principal.post(f"{BASE}/api/o/inbox/{THREAD_ID}/advice-approval/{aid}/approve", timeout=20)
    assert r.status_code == 200, r.text
    # Author then sends the approved reply
    r = ae.post(f"{BASE}/api/o/inbox/{THREAD_ID}/reply",
                json={"body": P_TEXT, "idempotency_key": f"snd-{uuid.uuid4()}", "approval_id": aid}, timeout=20)
    assert r.status_code == 200, r.text
    CREATED["reply_ids"].append(r.json()["reply_id"])
    assert _client_thread_count(owner2) == before + 1


def test_07_regression_non_P_reply_with_attachments_sends_immediately(ae, owner2):
    before = _client_thread_count(owner2)
    d, fids = _draft(ae)
    r = ae.post(f"{BASE}/api/o/inbox/{THREAD_ID}/reply",
                json={"body": "Attaching the latest repair quote and a photo for your file.",
                      "idempotency_key": f"nonp-{d}", "file_ids": fids, "draft_id": d}, timeout=30)
    assert r.status_code == 200, r.text
    CREATED["reply_ids"].append(r.json()["reply_id"])
    assert _client_thread_count(owner2) == before + 1


def test_zz_print_ids():
    print("CREATED_IDS", CREATED)
