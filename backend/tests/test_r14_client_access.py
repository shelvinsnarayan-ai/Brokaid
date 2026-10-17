"""R0.4 — A02 client-access tests 3, 4, 6 (live, against the running API).

Requires the API base URL in REACT_APP_BACKEND_URL (env) or frontend/.env, and
MONGO_URL / DB_NAME (backend/.env) for the audit-trail check. Skips if absent.
"""
import asyncio
import os
import uuid
from pathlib import Path

import httpx
import pytest
from dotenv import dotenv_values
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[2]
BASE = os.environ.get("REACT_APP_BACKEND_URL") or dotenv_values(ROOT / "frontend/.env").get("REACT_APP_BACKEND_URL")
MOTOR = "00000000-0000-4000-8000-000000000104"
BOOKKEEPER, OWNER = "bookkeeper@fictionalclient.example", "owner@fictionalclient.example"
# R1.3: read the per-run test DB (conftest sets it) — never the preview DB from backend/.env.
BENV = {"MONGO_URL": os.environ.get("MONGO_URL"), "DB_NAME": os.environ.get("DB_NAME")}


def _approved_envelope() -> str:
    """Arrange our own priced envelope deterministically (no live LLM), then Kai approves it."""
    if os.environ.get("R14_ENVELOPE_ID"):
        return os.environ["R14_ENVELOPE_ID"]
    env = str(uuid.uuid4())

    async def arrange():
        from motor.motor_asyncio import AsyncIOMotorClient

        from app.kernel import scene
        from app.kernel.tenant_ctx import use_tenant
        from app.kernel.trail_dispatch import append_via_dispatch
        from app.kernel.mocks.carrier_channel import submit_endorsement
        db = AsyncIOMotorClient(BENV["MONGO_URL"])[BENV["DB_NAME"]]
        pol = await db.policies.find_one({"policy_id": MOTOR})
        tid = pol["tenant_id"]
        await scene.emit_client_change_requested(
            db, tenant_id=tid, user_id="pytest", envelope_id=env, policy_id=MOTOR,
            client_id=pol["client_id"], raw_message="Raise my liability limit")
        quote = await submit_endorsement(envelope_id=env, submission={"requested_changes": []}, policy_current=pol)
        await scene.emit_carrier_quoted(db, tenant_id=tid, envelope_id=env, policy_id=MOTOR, quote=quote)
        await scene.emit_endorsement_quote_ready(db, tenant_id=tid, envelope_id=env, policy_id=MOTOR,
                                                 quote_id=quote["quote_id"])
        async with use_tenant(tid):  # the broker signs the class off, as /api/queue/{env}/resolve would
            await append_via_dispatch(db, tid, "OperatorApproval", actor={"kind": "human", "id": "pytest", "name": "principal"},
                                      payload={"envelope_id": env, "verdict": "approve", "edits": {}, "reason": "",
                                               "gate_reason": "operator_class_shadow_authoritative"},
                                      idempotency_key=f"op:approval:{env}")
    asyncio.run(arrange())
    r = _as(OWNER).post(f"/api/c/matters/{MOTOR}/approve/{env}")
    assert r.status_code == 200, r.text
    return env


@pytest.fixture(scope="module")
def signed_env() -> str:
    return _approved_envelope()


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


def test_3_bookkeeper_403_on_approve_and_sign_but_can_pay(signed_env):
    c = _as(BOOKKEEPER)
    r = c.post(f"/api/c/matters/{MOTOR}/approve/{signed_env}")
    assert r.status_code == 403 and _detail(r).get("code") == "AuthorityDenied", r.text
    r = c.post(f"/api/c/matters/{MOTOR}/sign/{signed_env}")
    assert r.status_code == 403 and _detail(r).get("code") == "AuthorityDenied", r.text
    r = c.post(f"/api/c/matters/{MOTOR}/paid/{signed_env}", json={})
    assert r.status_code == 200, r.text
    assert r.json()["ledger_event"]["type"] == "payment.advised"


def test_4_authority_events_written_and_second_attempt_also_403(signed_env):
    db = MongoClient(BENV["MONGO_URL"])[BENV["DB_NAME"]]
    c = _as(BOOKKEEPER)
    me = c.get("/api/c/me").json()
    uid = me["user"]["user_id"]
    before = db.authority_events.count_documents({"user_id": uid, "action": "approve_attempt", "outcome": "denied"})
    for _ in range(2):
        r = c.post(f"/api/c/matters/{MOTOR}/approve/{signed_env}")
        assert r.status_code == 403 and _detail(r).get("code") == "AuthorityDenied"
    rows = list(db.authority_events.find(
        {"user_id": uid, "action": "approve_attempt", "outcome": "denied"}).sort("ts", -1).limit(2))
    assert db.authority_events.count_documents(
        {"user_id": uid, "action": "approve_attempt", "outcome": "denied"}) == before + 2
    for row in rows:
        assert row["membership_id"] and row["policy_id"] == MOTOR and row["envelope_id"] == signed_env
        assert row["requested_authority"] == "can_approve" and row["actual_authority"]["can_approve"] is False


def test_6_me_returns_memberships():
    kai = _as(OWNER).get("/api/c/me").json()
    assert kai["memberships"], kai
    assert all(m["client_id"] and isinstance(m["authority"], dict) for m in kai["memberships"])
    assert any(m["role"] == "owner" and m["authority"]["can_approve"] for m in kai["memberships"])
    bk = _as(BOOKKEEPER).get("/api/c/me").json()
    rec = next(m for m in bk["memberships"] if m["role"] == "bookkeeper")
    assert rec["authority"]["can_pay"] is True and rec["authority"]["can_approve"] is False
