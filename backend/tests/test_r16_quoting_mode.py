"""R1.6 ruling — quoting_mode: a real (manual_only) tenant never produces MOCKED quotes, payment details or e-sign."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.auth.dispatch import create_session_scoped
from app.auth.sessions import COOKIE_NAME
from app.db.tenant_safe import wrap
from app.kernel import holidays, payments, renewals as rn
from app.kernel.agents.placement import PLACEMENT_ELIGIBLE_CLASSES
from app.kernel.tenant_ctx import use_tenant
from app.routes import c_scene
from tests.test_r16_renewals import _people, _tenant


async def _real_tenant(db):
    tid = await _tenant(db, f"realbroker-{uuid.uuid4().hex[:4]}", test=False)
    exp = datetime(2027, 5, 1, tzinfo=timezone.utc)
    async with use_tenant(tid):
        await wrap(db).clients.insert_one({"client_id": "c1", "display_name": "Real Client Pty Ltd"})
        await wrap(db).policies.insert_one({"policy_id": "p1", "client_id": "c1", "status": "managed", "product": "Motor",
                                            "insurer_display": "Real Insurer", "inception": exp.replace(year=2026), "expiry": exp,
                                            "premium_amount": 1000.0, "premium_currency": "AUD",
                                            "schedule": {"excess": {"any_one_occurrence": 500},
                                                         "vehicles": [{"rego": "AAA-111", "make": "Ford"}]}})
    return tid, exp


def test_defaults_by_tenant_kind():
    assert holidays.default_quoting_mode({"slug": "harbourline"}) == "mocked_allowed"
    assert holidays.default_quoting_mode({"slug": "import-test", "test": True}) == "mocked_allowed"
    assert holidays.default_quoting_mode({"slug": "burmac"}) == "manual_only"
    assert holidays.default_quoting_mode({"slug": "any-new-brokerage"}) == "manual_only"
    d = payments.details({"amount": "1", "currency": "AUD", "due_date": "x", "crn": "1", "state": "owed"}, None, "manual_only")
    assert d["mocked"] is False and d["eft"] is None and d["message"] == "Your broker will send payment details"


@pytest.mark.asyncio
async def test_renewal_in_manual_only_tenant_refuses_mocks(app_client, mongo_db):
    _, http = app_client
    db = mongo_db
    tid, exp = await _real_tenant(db)
    assert await holidays.quoting_mode(db, tid) == "manual_only"
    ids, ck = await _people(db, tid, "c1")
    r = await rn.start(db, tid, await rn._policy(db, tid, "p1"), now=exp - timedelta(days=60))
    kw = {"by": ids["ae"], "role": "ae"}
    r = await rn.accept_info(db, tid, r, "Confirmed by phone", **kw)
    with pytest.raises(rn.Bad, match="real insurer quotes"):
        await rn.request_quotes(db, tid, r, ["Real Insurer"], **kw)
    r = await rn.manual_quote(db, tid, r, {"insurer": "Real Insurer", "premium": "1050", "excess": "500"}, by_name="AE", **kw)
    assert not r["quotes"][0]["mocked"]
    r = await rn.recommend(db, tid, r, r["quotes"][0]["quote_id"], "Same cover and excess, small increase.", ["Same cover"], **kw)
    r = await rn.send_to_client(db, tid, r, **kw)
    d = (await http.post(f"/api/c/renewals/{r['renewal_id']}/decide", headers=ck["owner"], json={"choice": r["quotes"][0]["quote_id"]})).json()
    assert d["payment"]["pending_details"] and d["payment"]["eft"] is None and "MOCKED" not in str(d) and d["esign_available"] is False
    async with use_tenant(tid):
        await wrap(db).renewals.update_one({"renewal_id": r["renewal_id"]}, {"$set": {"stage": "paid"}})
    s = await http.post(f"/api/c/renewals/{r['renewal_id']}/sign", headers=ck["owner"])
    assert s.status_code == 409 and "isn't switched on" in s.text
    async with use_tenant(tid):
        assert await wrap(db).events.count_documents({"payload.mocked": True}) == 0


@pytest.mark.asyncio
async def test_endorsement_parks_for_enter_insurer_quote_then_proceeds(app_client, mongo_db):
    _, http = app_client
    db = mongo_db
    tid, _ = await _real_tenant(db)
    ids, ck = await _people(db, tid, "c1")
    env = str(uuid.uuid4())
    policy = await rn._policy(db, tid, "p1")
    change = [{"field_path": "schedule.vehicles[]", "operation": "add", "to_value": {"rego": "XYZ-789"}}]
    state = {"intake": {"payload": {"proposed_changes": change}, "class_key": sorted(PLACEMENT_ELIGIBLE_CLASSES)[0]}, "gates": "ok",
             "placement": {"structured_submission": {"requested_changes": change}}, "placement_gate": "ok", "submission": True}
    await c_scene._run_intake_placement_async(db, tenant_id=tid, user_id=ids["owner"], envelope_id_str=env, policy_id="p1",
                                              policy=policy, raw_message="Add XYZ-789", state=state)
    assert state["quote"] == "awaiting_manual"
    async with use_tenant(tid):
        safe = wrap(db)
        parked = await safe.events.find_one({"type": "RequestNeedsHumanReview", "payload.envelope_id": env})
        assert parked["payload"]["stage"] == "enter_insurer_quote" and "Enter insurer quote" in parked["payload"]["mismatch"]["reason"]
        assert not await safe.events.find_one({"type": "CarrierQuoted", "payload.envelope_id": env})
        assert await safe.proj_queue.find_one({"envelope_id": env, "resolved_at": None})
    ae = await create_session_scoped(db, tid, user_id=ids["ae"], brokerage_id="b", active_role="ae", mfa_verified=True,
                                     ttl=timedelta(minutes=10))
    h = {"Cookie": f"{COOKIE_NAME}={ae.session_id}"}
    bad = await http.post(f"/api/o/queue/{env}/insurer-quote", headers=h, json={"premium_change_amount": "eighty"})
    assert bad.status_code == 422
    q = await http.post(f"/api/o/queue/{env}/insurer-quote", headers=h,
                        json={"premium_change_amount": "92.40", "insurer": "Real Insurer", "reference": "RI-55"})
    assert q.status_code == 200, q.text
    assert q.json()["quote"]["mocked"] is False and q.json()["quote"]["data_basis"].startswith("manual entry by")
    assert (await http.post(f"/api/o/queue/{env}/insurer-quote", headers=h, json={"premium_change_amount": "1"})).status_code == 409
    assert (await http.post(f"/api/o/queue/{env}/approve", headers=h, json={"reason": "real quote"})).status_code == 200
    st = (await http.get("/api/c/matters/p1/status", headers=ck["owner"])).json()
    assert st["state"] == "awaiting_client_approval", st
    assert (await http.post(f"/api/c/matters/p1/approve/{env}", headers=ck["owner"])).status_code == 200
    pay = (await http.get(f"/api/c/matters/p1/pay/{env}", headers=ck["owner"])).json()["payment_details"]
    assert pay["amount"] == "92.40" and pay["pending_details"] and pay["eft"] is None
    assert (await http.post(f"/api/c/matters/p1/sign/{env}", headers=ck["owner"])).status_code == 409
