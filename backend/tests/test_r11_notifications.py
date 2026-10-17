"""R0.11A — in-app notifications pipeline (live API + kernel replay)."""
import asyncio
import os
import uuid
from pathlib import Path

import httpx
import pytest
from dotenv import dotenv_values, load_dotenv

from tests.test_r08_inbox_replies import _test_thread

ROOT = Path(__file__).resolve().parents[2]
BASE = os.environ.get("REACT_APP_BACKEND_URL") or dotenv_values(ROOT / "frontend/.env").get("REACT_APP_BACKEND_URL")
pytestmark = pytest.mark.skipif(not BASE, reason="live API not configured")
T = {"include_tests": True, "limit": 200}
MOTOR = "00000000-0000-4000-8000-000000000104"
RENEWAL_DEMO = "00000000-0000-4000-8000-000000000150"


def _as(email: str) -> httpx.Client:
    c = httpx.Client(base_url=BASE, timeout=30)
    assert c.post("/api/auth/dev/impersonate", json={"email": email}).status_code == 200
    return c


def _alerts(c, pred):
    import time
    for _ in range(8):  # R1.1: fan-out runs as a background job; give it a moment
        hits = [n for n in c.get("/api/notifications", params=T).json()["notifications"] if pred(n)]
        if hits:
            return hits
        time.sleep(0.5)
    return hits


def _kernel(coro_fn):
    load_dotenv(ROOT / "backend/.env")

    async def go():
        from motor.motor_asyncio import AsyncIOMotorClient
        db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
        tid = (await db.tenants.find_one({"slug": "harbourline"}))["tenant_id"]
        return await coro_fn(db, tid)
    return asyncio.run(go())


def test_question_alerts_staff_not_asker():
    tid = _test_thread()
    for who in ("ae", "principal"):
        hits = _alerts(_as(f"{who}@harbourline.example"), lambda n: n["link"] == f"/o/inbox/{tid}")
        assert len(hits) == 1 and hits[0]["kind"] == "client_question"
    assert not _alerts(_as("owner@fictionalclient.example"), lambda n: n["link"] == f"/o/inbox/{tid}")


def test_reply_alerts_members_coalesce_and_read():
    tid = _test_thread()
    cass = _as("ae@harbourline.example")
    for text in ("First reply (pytest).", "Second reply (pytest)."):
        assert cass.post(f"/api/o/inbox/{tid}/reply", json={"body": text, "idempotency_key": f"pytest-{uuid.uuid4()}"}).status_code == 200
    kai, book = _as("owner@fictionalclient.example"), _as("bookkeeper@fictionalclient.example")
    for c in (kai, book):
        hits = _alerts(c, lambda n: n["kind"] == "broker_reply" and not n["read"] and n["count"] == 2)
        assert hits and hits[0]["link"] == "/c/ask"
    mine = _alerts(kai, lambda n: n["kind"] == "broker_reply" and not n["read"])[0]
    r = kai.post(f"/api/notifications/{mine['id']}/read")
    assert r.status_code == 200 and r.json()["link"] == "/c/ask"
    assert not _alerts(kai, lambda n: n["id"] == mine["id"] and not n["read"])
    # test-thread alerts stay out of the default (demo) list
    assert all(n["id"] != mine["id"] for n in kai.get("/api/notifications").json()["notifications"])
    # someone else's notification → 404
    assert cass.post(f"/api/notifications/{mine['id']}/read").status_code == 404


def test_spine_recipients_and_replay():
    env = f"pytest-spine-{uuid.uuid4()}"

    async def go(db, tid):
        from app.kernel import notify
        for typ in ("RequestNeedsHumanReview", "OperatorApproval", "SignatureRequested", "CertificateRegenerated"):
            ev = f"pytest-ev-{typ}-{env}"
            for _ in range(2):  # replaying the same source event must not duplicate
                await notify.on_event(db, tid, typ, {"envelope_id": env, "policy_id": MOTOR, "test": True}, ev)
    _kernel(go)
    mine = lambda c: {n["kind"]: n for n in _alerts(c, lambda n: env in str(n.get("link")) or n["kind"] in  # noqa: E731
                                                     ("approval_needed", "signature_needed", "certificate_issued"))}
    cass = _alerts(_as("ae@harbourline.example"), lambda n: n["link"] == f"/o/queue/{env}")
    assert len(cass) == 1 and cass[0]["kind"] == "broker_review"
    kai = _as("owner@fictionalclient.example").get("/api/notifications", params=T).json()["notifications"]
    book = _as("bookkeeper@fictionalclient.example").get("/api/notifications", params=T).json()["notifications"]
    srcs = lambda rows, k: [n for n in rows if n["kind"] == k and n["updated_at"]]  # noqa: E731
    assert srcs(kai, "approval_needed") and srcs(kai, "signature_needed") and srcs(kai, "certificate_issued")
    assert not srcs(book, "approval_needed") and not srcs(book, "signature_needed")
    assert srcs(book, "certificate_issued")
    assert mine  # helper kept for readability


def test_overdue_and_renewal_alert_once():
    tid = _test_thread(days_ago=5)
    olly = _as("operator@harbourline.example")
    for _ in range(3):
        assert olly.post("/api/notifications/run-checks").status_code == 200
    for who in ("ae", "principal"):
        hits = _alerts(_as(f"{who}@harbourline.example"), lambda n: n["kind"] == "promise_overdue" and n["link"] == f"/o/inbox/{tid}")
        assert len(hits) == 1
    for who in ("ae@harbourline.example", "owner@fictionalclient.example"):
        assert len(_alerts(_as(who), lambda n: n["kind"] == "renewal_30" and "Tools and equipment" in n["body"])) == 1
    assert _as("ae@harbourline.example").post("/api/notifications/run-checks").status_code == 403


def test_prefs_counts_and_openapi():
    kai = _as("owner@fictionalclient.example")
    p = kai.get("/api/notifications/prefs").json()
    assert p["in_app"] is True and p["in_app_locked"] and p["quiet_hours"]["start"] == "21:00"
    assert kai.put("/api/notifications/prefs", json={"email": False, "push": False}).status_code == 200
    assert isinstance(kai.get("/api/notifications/unread-count").json()["count"], int)
    assert kai.post("/api/notifications/no-such-id/read").status_code == 404
    paths = set(httpx.get(f"{BASE}/api/openapi.json", timeout=30).json()["paths"])
    assert {"/api/notifications", "/api/notifications/unread-count", "/api/notifications/{notification_id}/read",
            "/api/notifications/read-all", "/api/notifications/prefs"} <= paths
