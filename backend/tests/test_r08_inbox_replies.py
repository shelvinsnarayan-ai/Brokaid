"""R0.8 — inbox replies + reply-by promises (live API + kernel rule)."""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from dotenv import dotenv_values, load_dotenv

from app.kernel import inbox

ROOT = Path(__file__).resolve().parents[2]
BASE = os.environ.get("REACT_APP_BACKEND_URL") or dotenv_values(ROOT / "frontend/.env").get("REACT_APP_BACKEND_URL")
KAI = "00000000-0000-4000-8000-000000000001"
live = pytest.mark.skipif(not BASE, reason="live API not configured")


def _as(email: str) -> httpx.Client:
    c = httpx.Client(base_url=BASE, timeout=30)
    assert c.post("/api/auth/dev/impersonate", json={"email": email}).status_code == 200
    return c


def _test_thread(days_ago: float = 0) -> str:
    """Kai test thread (flagged test → hidden from the demo inbox and client view by default)."""
    load_dotenv(ROOT / "backend/.env")

    async def go():
        from motor.motor_asyncio import AsyncIOMotorClient
        db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
        h = await db.tenants.find_one({"slug": "harbourline"})
        return await inbox.ask(db, h["tenant_id"], user_id="pytest", client_id=KAI, test=True,
                               message="Automated check: is my trailer covered?",
                               received_at=datetime.now(timezone.utc) - timedelta(days=days_ago))
    return asyncio.run(go())


def _syd(y, m, d, h):
    return datetime(y, m, d, h, tzinfo=inbox.SYD)


def test_business_day_rule():
    assert inbox.reply_by_for(_syd(2026, 10, 16, 15)) == _syd(2026, 10, 19, 15)  # Fri 3pm → Mon 3pm
    assert inbox.reply_by_for(_syd(2026, 10, 15, 9)) == _syd(2026, 10, 16, 9)    # Thu → Fri
    assert inbox.reply_by_for(_syd(2026, 10, 17, 10)) == _syd(2026, 10, 19, 10)  # Sat → Mon
    assert inbox.fmt_short(_syd(2026, 10, 19, 15)) == "19/10/2026 3:00 pm"


@live
def test_reply_idempotent_and_kept():
    tid = _test_thread()
    cass, key = _as("ae@harbourline.example"), f"pytest-{uuid.uuid4()}"
    r1 = cass.post(f"/api/o/inbox/{tid}/reply", json={"body": "Yes, the trailer is covered.", "idempotency_key": key})
    r2 = cass.post(f"/api/o/inbox/{tid}/reply", json={"body": "Yes, the trailer is covered.", "idempotency_key": key})
    assert r1.status_code == 200 and r2.status_code == 200 and r2.json()["replayed"] is True
    t = cass.get(f"/api/o/inbox/{tid}").json()
    assert t["status"] == "Answered" and t["kept"] == {"kept_at": t["kept"]["kept_at"], "late": False}
    assert [m["who"] for m in t["conversation"]].count("broker") == 1
    kai = _as("owner@fictionalclient.example").get("/api/c/ask/thread", params={"include_tests": True}).json()
    assert any(m["who"] == "broker" and m["thread_id"] == tid and "trailer is covered" in m["text"] for m in kai["thread"])


@live
def test_overdue_sorted_first_and_late_reply():
    tid = _test_thread(days_ago=5)
    cass = _as("ae@harbourline.example")
    rows = cass.get("/api/o/inbox", params={"include_tests": True}).json()["threads"]
    assert rows[0]["status"] == "Overdue"
    assert next(r for r in rows if r["thread_id"] == tid)["status"] == "Overdue"
    r = cass.post(f"/api/o/inbox/{tid}/reply", json={"body": "Sorry for the delay.", "idempotency_key": f"pytest-{uuid.uuid4()}"})
    assert r.status_code == 200 and r.json()["late"] is True


@live
def test_roles_and_validation():
    tid = _test_thread()
    olly = _as("operator@harbourline.example")
    assert olly.get(f"/api/o/inbox/{tid}").status_code == 200
    assert olly.post(f"/api/o/inbox/{tid}/reply", json={"body": "x", "idempotency_key": f"pytest-{uuid.uuid4()}"}).status_code == 403
    kai = _as("owner@fictionalclient.example")
    assert kai.get("/api/o/inbox").status_code == 403
    assert kai.get(f"/api/o/inbox/{tid}").status_code == 403
    assert kai.post(f"/api/o/inbox/{tid}/reply", json={"body": "x", "idempotency_key": f"pytest-{uuid.uuid4()}"}).status_code == 403
    ada = _as("principal@harbourline.example")
    for body in ("", "   ", "x" * 4001):
        assert ada.post(f"/api/o/inbox/{tid}/reply", json={"body": body, "idempotency_key": f"pytest-{uuid.uuid4()}"}).status_code == 422
    assert ada.post(f"/api/o/inbox/{tid}/reply", json={"body": "Ada here.", "idempotency_key": f"pytest-{uuid.uuid4()}"}).status_code == 200


@live
def test_openapi_lists_routes():
    paths = httpx.get(f"{BASE}/api/openapi.json", timeout=30).json()["paths"]
    assert {"/api/o/inbox", "/api/o/inbox/{thread_id}", "/api/o/inbox/{thread_id}/reply"} <= set(paths)
