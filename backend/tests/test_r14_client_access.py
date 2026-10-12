"""R0.4 — A02 client-access tests 3, 4, 6 (live, against the running API).

Requires the API base URL in REACT_APP_BACKEND_URL (env) or frontend/.env, and
MONGO_URL / DB_NAME (backend/.env) for the audit-trail check. Skips if absent.
"""
import os
from pathlib import Path

import httpx
import pytest
from dotenv import dotenv_values
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[2]
BASE = os.environ.get("REACT_APP_BACKEND_URL") or dotenv_values(ROOT / "frontend/.env").get("REACT_APP_BACKEND_URL")
BENV = dotenv_values(ROOT / "backend/.env")
MOTOR = "00000000-0000-4000-8000-000000000104"
# Envelope that already went approve → paid → sign (R0.3 vehicle-swap demo).
def _paid_envelope() -> str | None:
    """Latest envelope on Kai's motor policy that already has payment.advised."""
    if os.environ.get("R14_ENVELOPE_ID"):
        return os.environ["R14_ENVELOPE_ID"]
    if not BENV.get("MONGO_URL"):
        return None
    db = MongoClient(BENV["MONGO_URL"])[BENV["DB_NAME"]]
    kai = db.users.find_one({"email": "owner@fictionalclient.example"}) or {}
    ev = db.events.find_one({"type": "payment.advised", "tenant_id": kai.get("tenant_id"),
                             "payload.policy_id": "00000000-0000-4000-8000-000000000104"}, sort=[("seq", -1)])
    return (ev or {}).get("payload", {}).get("envelope_id")


SIGNED_ENV = _paid_envelope() or "00000000-0000-0000-0000-000000000000"
BOOKKEEPER, OWNER = "bookkeeper@fictionalclient.example", "owner@fictionalclient.example"

pytestmark = pytest.mark.skipif(not BASE or not BENV.get("MONGO_URL"), reason="live API/DB not configured")


def _as(email: str) -> httpx.Client:
    c = httpx.Client(base_url=BASE, timeout=30)
    r = c.post("/api/auth/dev/impersonate", json={"email": email})
    assert r.status_code == 200, r.text
    return c


def _detail(r: httpx.Response) -> dict:
    d = r.json().get("detail")
    return d if isinstance(d, dict) else {}


def test_3_bookkeeper_403_on_approve_and_sign_but_can_pay():
    c = _as(BOOKKEEPER)
    r = c.post(f"/api/c/matters/{MOTOR}/approve/{SIGNED_ENV}")
    assert r.status_code == 403 and _detail(r).get("code") == "AuthorityDenied", r.text
    r = c.post(f"/api/c/matters/{MOTOR}/sign/{SIGNED_ENV}")
    assert r.status_code == 403 and _detail(r).get("code") == "AuthorityDenied", r.text
    r = c.post(f"/api/c/matters/{MOTOR}/paid/{SIGNED_ENV}", json={})
    assert r.status_code == 200, r.text
    assert r.json()["ledger_event"]["type"] == "payment.advised"


def test_4_authority_events_written_and_second_attempt_also_403():
    db = MongoClient(BENV["MONGO_URL"])[BENV["DB_NAME"]]
    c = _as(BOOKKEEPER)
    me = c.get("/api/c/me").json()
    uid = me["user"]["user_id"]
    before = db.authority_events.count_documents({"user_id": uid, "action": "approve_attempt", "outcome": "denied"})
    for _ in range(2):
        r = c.post(f"/api/c/matters/{MOTOR}/approve/{SIGNED_ENV}")
        assert r.status_code == 403 and _detail(r).get("code") == "AuthorityDenied"
    rows = list(db.authority_events.find(
        {"user_id": uid, "action": "approve_attempt", "outcome": "denied"}).sort("ts", -1).limit(2))
    assert db.authority_events.count_documents(
        {"user_id": uid, "action": "approve_attempt", "outcome": "denied"}) == before + 2
    for row in rows:
        assert row["membership_id"] and row["policy_id"] == MOTOR and row["envelope_id"] == SIGNED_ENV
        assert row["requested_authority"] == "can_approve" and row["actual_authority"]["can_approve"] is False


def test_6_me_returns_memberships():
    kai = _as(OWNER).get("/api/c/me").json()
    assert kai["memberships"], kai
    assert all(m["client_id"] and isinstance(m["authority"], dict) for m in kai["memberships"])
    assert any(m["role"] == "owner" and m["authority"]["can_approve"] for m in kai["memberships"])
    bk = _as(BOOKKEEPER).get("/api/c/me").json()
    rec = next(m for m in bk["memberships"] if m["role"] == "bookkeeper")
    assert rec["authority"]["can_pay"] is True and rec["authority"]["can_approve"] is False
