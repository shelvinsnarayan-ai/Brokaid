"""R0.9 — unread broker replies badge API (live)."""
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


def _unread(c, tid):
    b = c.get("/api/c/ask/unread", params=T).json()
    return next((t["unread"] for t in b["threads"] if t["thread_id"] == tid), 0), b


def _reply(cass, tid, text):
    r = cass.post(f"/api/o/inbox/{tid}/reply", json={"body": text, "idempotency_key": f"pytest-{uuid.uuid4()}"})
    assert r.status_code == 200


def test_unread_counts_clear_per_user():
    tid = _test_thread()
    cass, kai, book = _as("ae@harbourline.example"), _as("owner@fictionalclient.example"), _as("bookkeeper@fictionalclient.example")
    assert _unread(kai, tid)[0] == 0
    _reply(cass, tid, "First reply.")
    assert _unread(kai, tid)[0] == 1
    _reply(cass, tid, "Second reply.")
    assert _unread(kai, tid)[0] == 2
    for _ in range(2):  # idempotent
        r = kai.post("/api/c/ask/seen", json={"thread_id": tid})
        assert r.status_code == 200
    assert _unread(kai, tid)[0] == 0
    assert _unread(book, tid)[0] == 2  # per user, not per client
    _reply(cass, tid, "Third reply.")
    assert _unread(kai, tid)[0] == 1


def test_test_threads_hidden_by_default():
    tid = _test_thread()
    _reply(_as("ae@harbourline.example"), tid, "Hidden test reply.")
    b = _as("owner@fictionalclient.example").get("/api/c/ask/unread").json()
    assert all(t["thread_id"] != tid for t in b["threads"])
    assert b["count"] == sum(t["unread"] for t in b["threads"])


def test_operator_roles_forbidden():
    for who in ("ae", "principal", "operator"):
        c = _as(f"{who}@harbourline.example")
        assert c.get("/api/c/ask/unread").status_code == 403
        assert c.post("/api/c/ask/seen", json={"thread_id": "seed-ask-kai-2"}).status_code == 403
    assert _as("owner@fictionalclient.example").post("/api/c/ask/seen", json={"thread_id": "no-such-thread"}).status_code == 404
