"""R1.7 — compliance records: disclosure docs + delivery, DDO TMD-fit, GAW/SoA, complaints clock, privacy export, retention, eval."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db.tenant_safe import wrap
from app.kernel import complaints as cm
from app.kernel import compliance_docs as cd
from app.kernel import object_store, privacy_export, renewals as rn, retention
from app.kernel.tenant_ctx import use_tenant
from tests.test_r16_renewals import _count, _people, _tenant

BLOBS: dict[str, bytes] = {}


@pytest.fixture(autouse=True)
def fake_storage(monkeypatch):
    async def put(path, data, ct):
        BLOBS[path] = data
        return {"path": path}

    async def get(path):
        return BLOBS[path], "application/octet-stream"
    monkeypatch.setattr(object_store, "put_object", put)
    monkeypatch.setattr(object_store, "get_object", get)


async def _setup(db, retail=True):
    tid = await _tenant(db, f"r17-{uuid.uuid4().hex[:4]}")
    exp = datetime(2027, 6, 30, tzinfo=timezone.utc)
    async with use_tenant(tid):
        await wrap(db).clients.insert_one({"client_id": "c1", "display_name": "Retail Rita"})
        await wrap(db).policies.insert_one({"policy_id": "p1", "client_id": "c1", "status": "managed", "product": "Home & Contents",
                                            "insurer_display": "Fictional Carrier A", "inception": exp.replace(year=2026), "expiry": exp,
                                            "premium_amount": 1500, "schedule": {"excess": {"any_one_occurrence": 500}}})
    ids, ck = await _people(db, tid, "c1")
    if retail:
        async with use_tenant(tid):
            await wrap(db).clients.update_one({"client_id": "c1"}, {"$unset": {"client_type": "", "client_type_reason": ""}})
    return tid, exp, ids, ck


async def _upload(db, tid, kind, insurer=None, version="1.0"):
    return await cd.upload(db, tid, kind=kind, data=f"%PDF-1.4 {kind} {insurer} {version}".encode(), filename=f"{kind}.pdf",
                           content_type="application/pdf", version=version, effective_date="2026-07-01", insurer=insurer,
                           product=None, by="principal")


@pytest.mark.asyncio
async def test_retail_renewal_needs_tmd_check_and_pds_tmd_delivered(app_client, mongo_db):
    _, http = app_client
    db = mongo_db
    tid, exp, ids, ck = await _setup(db)
    assert (await cd.client_type(db, tid, "c1"))["client_type"] == "retail"
    kw = {"by": ids["ae"], "role": "ae"}
    r = await rn.start(db, tid, await rn._policy(db, tid, "p1"), now=exp - timedelta(days=60))
    r = await rn.accept_info(db, tid, r, "Confirmed by phone", **kw)
    r = await rn.manual_quote(db, tid, r, {"insurer": "Fictional Carrier A", "premium": "1560", "excess": "500"}, by_name="AE", **kw)
    q = r["quotes"][0]["quote_id"]
    text = "Same cover and excess as expiring at a small increase."
    with pytest.raises(rn.Bad, match="TMD-fit"):
        await rn.recommend(db, tid, r, q, text, ["Same cover"], **kw)
    r = await rn.tmd_check(db, tid, r, "no", "Client rents; product targets owner-occupiers", **kw)
    with pytest.raises(rn.Bad, match="Principal must approve"):  # DDO "No" is blocked without the Principal
        await rn.recommend(db, tid, r, q, text, ["Same cover"], **kw)
    r = await rn.approve_tmd(db, tid, r, by=ids["principal"])
    async with use_tenant(tid):
        await wrap(db).tenant_settings.update_one({}, {"$set": {"gaw_text": "HARBOURLINE GAW v2"}}, upsert=True)
    r = await rn.recommend(db, tid, r, q, text, ["Same cover"], **kw)
    assert r["recommendation"]["tier"] == "G" and r["recommendation"]["general_advice_warning"] == "HARBOURLINE GAW v2"
    with pytest.raises(rn.Bad, match="PDS for Fictional Carrier A"):  # retail: options can't go out without PDS + TMD
        await rn.send_to_client(db, tid, r, **kw)
    assert await _count(db, tid, "DocumentDelivered") == 0
    await _upload(db, tid, "FSG")
    await _upload(db, tid, "PDS", "Fictional Carrier A")
    await _upload(db, tid, "TMD", "Fictional Carrier A")
    r = await rn.send_to_client(db, tid, r, **kw)
    assert r["stage"] == "client_decision"
    assert await _count(db, tid, "DocumentDelivered") == 3 and await _count(db, tid, "GeneralAdviceWarningGiven") == 1
    docs = (await http.get("/api/c/documents", headers=ck["owner"])).json()["documents"]
    assert {d["kind"] for d in docs} == {"FSG", "PDS", "TMD"}
    dl = await http.get(f"/api/c/documents/download?d={docs[0]['delivery_id']}", headers=ck["owner"])
    assert dl.status_code == 200 and dl.content.startswith(b"%PDF")
    view = (await http.get(f"/api/c/renewals/{r['renewal_id']}", headers=ck["owner"])).json()
    assert view["recommendation"]["general_advice_warning"] == "HARBOURLINE GAW v2"
    assert (await http.post(f"/api/c/renewals/{r['renewal_id']}/decide", headers=ck["owner"], json={"choice": q})).status_code == 200
    async with use_tenant(tid):  # significant dealing outside the TMD: flag + report log + Principal alert
        assert await wrap(db).ddo_reports.count_documents({"renewal_id": r["renewal_id"]}) == 1
        assert await wrap(db).notifications.count_documents({"user_id": ids["principal"], "kind": "renewal_ddo"}) == 1
    # a new FSG version is delivered again on the next onboarding
    v2 = await _upload(db, tid, "FSG", version="2.0")
    d = await cd.on_client_onboarded(db, tid, "c1", ids["owner"], "invite_acceptance")
    assert d["doc_id"] == v2["doc_id"] and await _count(db, tid, "CollectionNoticeRecorded") == 1


@pytest.mark.asyncio
async def test_personal_advice_carries_soa_note(mongo_db):
    db = mongo_db
    tid, exp, ids, _ = await _setup(db, retail=False)
    kw = {"by": ids["ae"], "role": "ae"}
    r = await rn.start(db, tid, await rn._policy(db, tid, "p1"), now=exp - timedelta(days=60))
    r = await rn.accept_info(db, tid, r, "Confirmed", **kw)
    r = await rn.manual_quote(db, tid, r, {"insurer": "Fictional Carrier A", "premium": "1560", "excess": "500"}, by_name="AE", **kw)
    r = await rn.recommend(db, tid, r, r["quotes"][0]["quote_id"], "Given your budget we recommend you stay.", ["Budget"], **kw)
    assert r["recommendation"]["tier"] == "P" and "Statement of Advice" in r["recommendation"]["soa_note"]
    assert r["recommendation"]["general_advice_warning"] is None


@pytest.mark.asyncio
async def test_complaint_clock_alerts_afca_and_visibility(app_client, mongo_db):
    _, http = app_client
    db = mongo_db
    c = cm.clock(datetime(2026, 12, 24, 3, tzinfo=timezone.utc), "NSW")  # Thu 24 Dec 2026 2pm Sydney
    assert c["ack_due"].astimezone(cm.holidays.SYD).date().isoformat() == "2026-12-29"  # Xmas Fri, Boxing (obs) Mon skipped
    assert c["response_due"].date().isoformat() == "2027-01-23"
    tid, _, ids, ck = await _setup(db)
    res = await http.post("/api/c/complaints", headers=ck["owner"], json={"category": "service", "summary": "No reply for 2 weeks",
                                                                          "details": "Private details"})
    assert res.status_code == 200, res.text
    m = await cm.get(db, tid, "complaint", res.json()["matter_id"])
    async with use_tenant(tid):
        safe = wrap(db)
        assert await safe.notifications.count_documents({"user_id": ids["principal"], "kind": "complaint_clock"}) == 1
        ev = await safe.events.find_one({"type": "ComplaintStageChanged", "payload.matter_id": m["matter_id"]})
    assert "details" not in json.dumps(ev["payload"], default=str) and ev["payload"]["content_sha256"]
    due = cm._utc(m["response_due"])
    assert await cm.tick(db, tid, cm._utc(m["ack_due"]) + timedelta(minutes=1)) == 1  # not acknowledged in 1 business day
    assert await cm.tick(db, tid, due - timedelta(days=5)) == 1
    assert await cm.tick(db, tid, due - timedelta(days=5)) == 0
    assert await cm.tick(db, tid, due - timedelta(days=2)) == 1
    assert await cm.tick(db, tid, due + timedelta(hours=1)) == 1
    m2 = await cm.get(db, tid, "complaint", m["matter_id"])
    assert {"ack_overdue", "due5", "due2", "overdue"} <= set(m2["alerts"])
    async with use_tenant(tid):  # alerts coalesce into one card per matter per person (dedupe_key)
        n = await wrap(db).notifications.count_documents({"user_id": {"$in": [ids["ae"], ids["principal"]]}, "kind": "complaint_clock"})
    assert n == 2
    for to in ("acknowledged", "investigating"):
        m = await cm.move(db, tid, "complaint", m, to, by=ids["ae"], role="ae")
    with pytest.raises(cm.Bad):
        await cm.move(db, tid, "complaint", m, "response_sent", by=ids["ae"], role="ae", response_text="Sorry.")
    afca = (await cd.settings(db, tid))["afca_text"]
    m = await cm.move(db, tid, "complaint", m, "response_sent", by=ids["ae"], role="ae", outcome="resolved_in_client_favour",
                      response_text="We apologise and have fixed the delay.", afca_text=afca)
    assert afca in m["response_text"] and "AFCA" in afca and "1800 931 678" in afca
    m = await cm.move(db, tid, "complaint", m, "closed", by=ids["ae"], role="ae")
    rows = await cm.register(db, tid, "complaint")
    stats = cm.idr_stats(rows)
    assert stats["by_category"] == {"service": 1} and stats["by_outcome"] == {"resolved_in_client_favour": 1}
    other = type("S", (), {"active_role": "ae", "user_id": "someone-else"})()
    assert await cm.register(db, tid, "complaint", other) == []  # content only for Principal / assigned / logger
    mine = (await http.get("/api/c/complaints", headers=ck["owner"])).json()
    assert mine["complaints"][0]["stage"] == "closed" and afca in mine["complaints"][0]["response_text"]


@pytest.mark.asyncio
async def test_privacy_access_export_is_kai_only(seeded, app_client, mongo_db):
    _, http = app_client
    db = mongo_db
    kai = await db.policies.find_one({"policy_id": "00000000-0000-4000-8000-000000000104"})  # tenant-safe: system-only
    tid, kai_client = kai["tenant_id"], kai["client_id"]
    ids, ck = await _people(db, tid, kai_client)
    res = await http.post("/api/c/my-data/requests", headers=ck["owner"], json={"kind": "access"})
    assert res.status_code == 200 and res.json()["export"] == "queued"
    mid = res.json()["matter_id"]
    async with use_tenant(tid):
        job = await wrap(db).jobs.find_one({"kind": "privacy.export", "payload.matter_id": mid})
    assert job
    await privacy_export.build(db, tid, mid, kai_client, ids["owner"])
    ex = (await http.get("/api/c/my-data", headers=ck["owner"])).json()["exports"][0]
    assert not ex["expired"] and (cm._utc(datetime.fromisoformat(ex["expires_at"])) - datetime.now(timezone.utc)).days >= 6
    data = (await http.get(f"/api/c/my-data/export?e={ex['export_id']}&fmt=json", headers=ck["owner"])).json()
    assert data["client"]["client_id"] == kai_client and data["policies"]
    async with use_tenant(tid):
        others = [c["client_id"] async for c in wrap(db).clients.find({"client_id": {"$ne": kai_client}}, {"client_id": 1})]
    blob = json.dumps(data)
    assert others and not any(o in blob for o in others) and "password_hash" not in blob
    assert all(p["client_id"] == kai_client for p in data["policies"])
    pdf = await http.get(f"/api/c/my-data/export?e={ex['export_id']}&fmt=pdf", headers=ck["owner"])
    assert pdf.content.startswith(b"%PDF")
    # another client's owner in the same tenant can't fetch Kai's export
    async with use_tenant(tid):
        await wrap(db).clients.insert_one({"client_id": "other-c", "display_name": "Other"})
    _, ck2 = await _people(db, tid, "other-c")
    assert (await http.get(f"/api/c/my-data/export?e={ex['export_id']}&fmt=json", headers=ck2["owner"])).status_code == 404
    async with use_tenant(tid):
        await wrap(db).privacy_exports.update_one({"export_id": ex["export_id"]}, {"$set": {"expires_at": datetime.now(timezone.utc)}})
    assert (await http.get(f"/api/c/my-data/export?e={ex['export_id']}&fmt=json", headers=ck["owner"])).status_code == 410


@pytest.mark.asyncio
async def test_retention_report_hold_and_redaction(mongo_db):
    db = mongo_db
    tid = await _tenant(db, f"ret-{uuid.uuid4().hex[:4]}")
    old = datetime(2017, 3, 1, tzinfo=timezone.utc)
    async with use_tenant(tid):
        for cid in ("gone", "held"):
            await wrap(db).clients.insert_one({"client_id": cid, "display_name": f"Old {cid}", "email": f"{cid}@x.example"})
            await wrap(db).policies.insert_one({"policy_id": f"p-{cid}", "client_id": cid, "status": "lapsed", "expiry": old})
    await retention.set_hold(db, tid, "held", True, "Litigation", "principal")
    rep = await retention.report(db, tid)
    past = {(r["record_type"], r["id"]): r for r in rep["past_retention"]}
    assert past[("client", "gone")]["redactable"] and not past[("client", "held")]["redactable"] and rep["auto_delete"] is False
    before = await _count(db, tid, "PolicyCreated")
    with pytest.raises(ValueError):
        await retention.redact(db, tid, "held", "approved", "principal")
    out = await retention.redact(db, tid, "gone", "Past 7 years; approved", "principal")
    assert "email" in out["fields"] and await _count(db, tid, "RecordRedacted") == 1 and await _count(db, tid, "PolicyCreated") == before
    async with use_tenant(tid):
        c = await wrap(db).clients.find_one({"client_id": "gone"})
    assert c["display_name"].startswith("Redacted client") and c["email"] is None


@pytest.mark.asyncio
async def test_eval_scoring_and_gate_status(mongo_db):
    from app.eval import gate, run as ev

    class Out:
        def __init__(self, changes):
            self.payload, self.class_key = {"proposed_changes": changes}, "x"
            self.tokens_used = type("T", (), {"input": 1000, "output": 200})()

    class Brain:
        async def invoke(self, env, agent):
            msg = env.inputs["raw_message"]
            if "ZZZ" in msg:
                return Out([{"field_path": "schedule.vehicles[]", "operation": "remove", "from_value": {"rego": "ZZZ-999"}}])
            if "Navara off" in msg:  # removal by description resolves to the one held Navara
                return Out([{"field_path": "schedule.vehicles[]", "operation": "remove", "from_value": {"model": "Navara"}},
                            {"field_path": "schedule.vehicles[]", "operation": "add", "from_value": None, "to_value": {"rego": "LC-7001"}}])
            return Out([])
    cases = {c["id"]: c for c in ev.CASES}
    assert len(ev.CASES) >= 60 and sum(c["safety"] for c in ev.CASES) >= 20
    assert {t for c in ev.CASES for t in c["tags"]} >= {"r0_11", "invented", "injection", "ambiguous", "limit", "certificate", "out_of_scope"}
    tid = str(uuid.uuid4())
    r1 = await ev.run_case(Brain(), tid, cases["inv-01"])
    assert r1["pass"] and r1["got"]["guard"] == "unmatched" and r1["cost_usd"] > 0
    assert (await ev.run_case(Brain(), tid, cases["swap-07"]))["pass"]
    assert not (await ev.run_case(Brain(), tid, cases["swap-01"]))["pass"]  # a model that proposes nothing fails the swap
    assert len(gate.prompt_hash()) == 64
    st = await gate.status(mongo_db)
    assert st["last"] is None and not st["ok_to_release"]
