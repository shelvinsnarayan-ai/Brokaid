"""R0.10a — reply highlight inputs (unread reply ids, per user) + test-data flags."""
import os
import uuid
from pathlib import Path

import httpx
import pytest
from dotenv import dotenv_values

from tests.test_r08_inbox_replies import _test_thread

ROOT = Path(__file__).resolve().parents[2]
BASE = os.environ.get("REACT_APP_BACKEND_URL") or dotenv_values(ROOT / "frontend/.env").get("REACT_APP_BACKEND_URL")
pytestmark = pytest.mark.skipif(not BASE, reason="live API not configured")
T = {"include_tests": True}


def _as(email: str) -> httpx.Client:
    c = httpx.Client(base_url=BASE, timeout=30)
    assert c.post("/api/auth/dev/impersonate", json={"email": email}).status_code == 200
    return c


def _ids(c, tid):
    b = c.get("/api/c/ask/unread", params=T).json()
    return next((t["reply_ids"] for t in b["threads"] if t["thread_id"] == tid), [])


def test_unread_reply_ids_snapshot_then_seen():
    tid = _test_thread()
    cass, kai, book = _as("ae@harbourline.example"), _as("owner@fictionalclient.example"), _as("bookkeeper@fictionalclient.example")
    rid = cass.post(f"/api/o/inbox/{tid}/reply", json={"body": "Highlight me.", "idempotency_key": f"pytest-{uuid.uuid4()}"}).json()["reply_id"]
    assert _ids(kai, tid) == [rid]
    thread = kai.get("/api/c/ask/thread", params=T).json()["thread"]
    assert any(m.get("reply_id") == rid for m in thread)  # the page can match ids to bubbles
    assert kai.post("/api/c/ask/seen", json={"thread_id": tid}).status_code == 200
    assert _ids(kai, tid) == []          # reload → no highlight
    assert _ids(book, tid) == [rid]      # per user
    rid2 = cass.post(f"/api/o/inbox/{tid}/reply", json={"body": "Only the new one.", "idempotency_key": f"pytest-{uuid.uuid4()}"}).json()["reply_id"]
    assert _ids(kai, tid) == [rid2]      # previously seen reply never re-highlighted


def test_flagged_test_data_hidden_from_client():
    thread = _as("owner@fictionalclient.example").get("/api/c/ask/thread").json()["thread"]
    text = " ".join(m.get("text") or "" for m in thread)
    for marker in ("BROWSERTEST", "BROWSERREPLY", "JTREPLY", "R09 test"):
        assert marker not in text
    assert all(m["who"] != "broker" or m.get("reply_id") or m.get("attribution") for m in thread)
