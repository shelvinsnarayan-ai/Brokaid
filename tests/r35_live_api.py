"""R3.5 live API smoke tests against preview (dev impersonation).
Tests upload magic-byte rejection, cross-client 404, closed-claim 409, 401 unauth, replay msg_id."""
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
OPEN_CLAIM = "a3cbd499-6a45-4ca4-9b17-f8d62b45ef40"  # CLM-2026-000013 (DO NOT POST)
WITHDRAWN_CLAIM_REF = "CLM-2026-000024"


def _impersonate(email: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/dev/impersonate", json={"email": email}, timeout=20)
    assert r.status_code == 200, f"impersonate {email}: {r.status_code} {r.text}"
    return s


def _tiny_png() -> bytes:
    # minimal valid 1x1 PNG
    sig = b"\x89PNG\r\n\x1a\n"
    def _chunk(t, data):
        return struct.pack(">I", len(data)) + t + data + struct.pack(">I", zlib.crc32(t + data) & 0xFFFFFFFF)
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    idat = _chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
    iend = _chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


@pytest.fixture(scope="module")
def owner():
    return _impersonate(OWNER)


@pytest.fixture(scope="module")
def owner2():
    return _impersonate(OWNER2)


@pytest.fixture(scope="module")
def ae():
    return _impersonate(AE)


# --- Thread GET: demo message with 2 photos ---
def test_demo_thread_has_two_photo_attachments(owner):
    r = owner.get(f"{BASE}/api/c/claims/{OPEN_CLAIM}/thread", timeout=20)
    assert r.status_code == 200, r.text
    msgs = r.json()["messages"]
    seed = next((m for m in msgs if m["who"] == "client" and "rear bumper" in (m.get("text") or "").lower()), None)
    assert seed, f"Seeded 'rear bumper' message not found in {[m.get('text') for m in msgs]}"
    atts = seed.get("attachments") or []
    assert len(atts) == 2, f"Expected 2 attachments, got {len(atts)}"
    assert all(a["kind"] == "photo" for a in atts)
    # authenticated serving works
    f0 = atts[0]["file_id"]
    r2 = owner.get(f"{BASE}/api/c/claims/{OPEN_CLAIM}/files/{f0}", timeout=20)
    assert r2.status_code == 200
    assert r2.headers.get("content-type", "").startswith("image/")


# --- Cross-client 404 for owner2 hitting owner's claim file ---
def test_cross_client_file_404(owner, owner2):
    r = owner.get(f"{BASE}/api/c/claims/{OPEN_CLAIM}/thread", timeout=20)
    seed = next(m for m in r.json()["messages"] if "rear bumper" in (m.get("text") or "").lower())
    f0 = seed["attachments"][0]["file_id"]
    r2 = owner2.get(f"{BASE}/api/c/claims/{OPEN_CLAIM}/files/{f0}", timeout=20)
    assert r2.status_code == 404, f"Expected 404 cross-client, got {r2.status_code}"


# --- Unauthenticated 401 ---
def test_unauth_file_401():
    r = requests.get(f"{BASE}/api/c/claims/{OPEN_CLAIM}/files/anything", timeout=20)
    assert r.status_code == 401, r.status_code


# --- Magic-byte: disguised PDF named .jpg → 415 ---
def test_disguised_pdf_415(owner2):
    # list owner2 claims to find an open one
    r = owner2.get(f"{BASE}/api/c/claim-threads", timeout=20)
    assert r.status_code == 200, r.text
    open_cs = [t for t in r.json()["threads"] if t["open"]]
    assert open_cs, "owner2 has no open claim"
    cid = open_cs[0]["claim_id"]
    msg_id = str(uuid.uuid4())
    r = owner2.post(
        f"{BASE}/api/c/claims/{cid}/thread/attachments",
        data={"msg_id": msg_id},
        files={"file": ("photo.jpg", b"%PDF-1.7\n" + b"x" * 500, "image/jpeg")},
        timeout=30,
    )
    assert r.status_code == 415, f"disguised PDF: {r.status_code} {r.text}"


# --- Fake MP4 (ftyp + zeros) → 415 ---
def test_fake_mp4_415(owner2):
    r = owner2.get(f"{BASE}/api/c/claim-threads", timeout=20)
    cid = next(t for t in r.json()["threads"] if t["open"])["claim_id"]
    msg_id = str(uuid.uuid4())
    fake = b"\x00\x00\x00\x18ftypmp42" + b"\0" * 2000
    r = owner2.post(
        f"{BASE}/api/c/claims/{cid}/thread/attachments",
        data={"msg_id": msg_id},
        files={"file": ("x.mp4", fake, "video/mp4")},
        timeout=30,
    )
    assert r.status_code == 415, f"fake mp4: {r.status_code} {r.text}"


# --- Oversize photo (>10MB) → 413 ---
def test_oversize_photo_413(owner2):
    r = owner2.get(f"{BASE}/api/c/claim-threads", timeout=20)
    cid = next(t for t in r.json()["threads"] if t["open"])["claim_id"]
    msg_id = str(uuid.uuid4())
    big = _tiny_png() + b"\0" * (10 * 1024 * 1024 + 100)
    r = owner2.post(
        f"{BASE}/api/c/claims/{cid}/thread/attachments",
        data={"msg_id": msg_id},
        files={"file": ("big.png", big, "image/png")},
        timeout=60,
    )
    assert r.status_code == 413, f"oversize: {r.status_code} {r.text[:200]}"
    body = r.json()
    assert body.get("detail", {}).get("code") == "too_large"


# --- Closed/withdrawn claim 409 ---
def test_closed_claim_409(owner):
    r = owner.get(f"{BASE}/api/c/claim-threads", timeout=20)
    assert r.status_code == 200
    threads = r.json()["threads"]
    # find CLM-2026-000024
    closed = next((t for t in threads if t.get("claim_reference") == WITHDRAWN_CLAIM_REF), None)
    assert closed, f"Withdrawn claim {WITHDRAWN_CLAIM_REF} not found; threads: {[t.get('claim_reference') for t in threads]}"
    cid = closed["claim_id"]
    msg_id = str(uuid.uuid4())
    r = owner.post(
        f"{BASE}/api/c/claims/{cid}/thread/attachments",
        data={"msg_id": msg_id},
        files={"file": ("a.png", _tiny_png(), "image/png")},
        timeout=30,
    )
    assert r.status_code == 409, f"closed: {r.status_code} {r.text[:200]}"
    assert r.json()["detail"]["code"] == "claim_closed"


# --- Upload a real PNG, send text+attachment, replay msg_id, then try more upload = 409 already_sent ---
POSTED_MSG_IDS: list[tuple[str, str]] = []  # (claim_id, msg_id)


def test_full_upload_and_send_flow(owner2):
    s = owner2
    r = s.get(f"{BASE}/api/c/claim-threads", timeout=20)
    cid = next(t for t in r.json()["threads"] if t["open"])["claim_id"]
    msg_id = str(uuid.uuid4())
    # Upload PNG
    up = s.post(
        f"{BASE}/api/c/claims/{cid}/thread/attachments",
        data={"msg_id": msg_id},
        files={"file": ("test.png", _tiny_png(), "image/png")},
        timeout=30,
    )
    assert up.status_code == 200, up.text
    file_id = up.json()["file"]["file_id"]
    assert up.json()["file"]["content_type"] == "image/png"

    # Send message with text + attachment
    send = s.post(
        f"{BASE}/api/c/claims/{cid}/thread",
        json={"message": "QA R3.5 test attachment", "file_ids": [file_id], "msg_id": msg_id},
        timeout=30,
    )
    assert send.status_code == 200, send.text
    POSTED_MSG_IDS.append((cid, msg_id))

    # Replay same msg_id -> replayed true
    again = s.post(
        f"{BASE}/api/c/claims/{cid}/thread",
        json={"message": "QA R3.5 test attachment", "file_ids": [file_id], "msg_id": msg_id},
        timeout=30,
    )
    assert again.status_code == 200 and again.json().get("replayed") is True, again.text

    # Upload to already-sent msg_id -> 409 already_sent
    late = s.post(
        f"{BASE}/api/c/claims/{cid}/thread/attachments",
        data={"msg_id": msg_id},
        files={"file": ("late.png", _tiny_png(), "image/png")},
        timeout=30,
    )
    assert late.status_code == 409, late.text
    assert late.json()["detail"]["code"] == "already_sent"


def test_attachments_only_message(owner2):
    s = owner2
    r = s.get(f"{BASE}/api/c/claim-threads", timeout=20)
    cid = next(t for t in r.json()["threads"] if t["open"])["claim_id"]
    msg_id = str(uuid.uuid4())
    up = s.post(
        f"{BASE}/api/c/claims/{cid}/thread/attachments",
        data={"msg_id": msg_id},
        files={"file": ("ao.png", _tiny_png(), "image/png")},
        timeout=30,
    )
    assert up.status_code == 200, up.text
    fid = up.json()["file"]["file_id"]
    send = s.post(
        f"{BASE}/api/c/claims/{cid}/thread",
        json={"file_ids": [fid], "msg_id": msg_id},
        timeout=30,
    )
    assert send.status_code == 200, send.text
    POSTED_MSG_IDS.append((cid, msg_id))


def test_report_posted_msg_ids():
    print("\n=== POSTED MSG_IDS (for test-flagging) ===")
    for cid, mid in POSTED_MSG_IDS:
        print(f"  claim_id={cid}  msg_id={mid}")
    print("===========================================")


# --- Staff AE sees attachments on claim detail (files tagged claim_thread) ---
def test_staff_sees_thread_attachments(ae):
    # staff view of CLM-2026-000013 should include claim_thread files
    r = ae.get(f"{BASE}/api/o/claims/{OPEN_CLAIM}", timeout=20)
    assert r.status_code == 200, r.text
    body = r.json()
    claim = body.get("claim") or body
    files = claim.get("files") or []
    thread_files = [f for f in files if f.get("source") == "claim_thread"]
    assert thread_files, f"No claim_thread-sourced files for staff on {OPEN_CLAIM}"

    # staff Messages panel
    r = ae.get(f"{BASE}/api/o/claims/{OPEN_CLAIM}/thread", timeout=20)
    assert r.status_code == 200, r.text
    conv = r.json().get("conversation") or []
    assert any(m.get("attachments") for m in conv), "Staff thread shows no attachments"
