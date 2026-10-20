"""R1.8 — claims: Kai's motor (Hilux) claim with photos to settlement via trust, flags not denials, authority, isolation,
declined → IDR/AFCA path, manual_only refuses the MOCKED insurer channel, SLA tick, broker-reply GAW / P approval."""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image

from app.db.tenant_safe import wrap
from app.kernel import cert_stp, inbox, object_store
from app.kernel import claims as ck
from app.kernel.tenant_ctx import use_tenant
from tests.test_r16_renewals import _count, _people, _tenant

BLOBS: dict[str, bytes] = {}
TODAY = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def fake_storage(monkeypatch):
    async def put(path, data, ct):
        BLOBS[path] = data
        return {"path": path}

    async def get(path):
        return BLOBS[path], "application/octet-stream"
    monkeypatch.setattr(object_store, "put_object", put)
    monkeypatch.setattr(object_store, "get_object", get)


def _jpeg_with_gps() -> bytes:
    im = Image.new("RGB", (40, 30), (200, 10, 10))
    exif = Image.Exif()
    exif[0x8825] = {1: "S", 2: (33.0, 52.0, 0.0), 3: "E", 4: (151.0, 12.0, 0.0)}  # GPS IFD
    buf = io.BytesIO()
    im.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


async def _setup(db, test=True, slug=None):
    tid = await _tenant(db, slug or f"r18-{uuid.uuid4().hex[:4]}", test=test)
    async with use_tenant(tid):
        await wrap(db).clients.insert_one({"client_id": "kai", "display_name": "Kai's Marine"})
        await wrap(db).clients.insert_one({"client_id": "other", "display_name": "Other Client"})
        await wrap(db).policies.insert_one({
            "policy_id": "motor", "client_id": "kai", "status": "managed", "product": "Motor — Commercial Vehicle",
            "insurer_display": "Fictional Carrier A", "inception": TODAY - timedelta(days=60), "expiry": TODAY + timedelta(days=300),
            "schedule": {"excess": {"any_one_occurrence": 850}, "vehicles": [
                {"rego": "ABC-123", "make": "Toyota", "model": "Hilux SR5", "excess": 850},
                {"rego": "DEF-456", "make": "Isuzu", "model": "D-Max", "excess": 850}]}})
    ids, cookies = await _people(db, tid, "kai")
    return tid, ids, cookies


FNOL = {"policy_id": "motor", "claim_class": "motor", "subtype": "accident", "incident_date": TODAY.date().isoformat(),
        "where": "Wharf Road, Fictional Bay", "description": "Rear-ended at the lights, tailgate and bumper damaged.",
        "parties": [{"name": "Pat Example", "rego": "XYZ-789", "insurer": "Example Mutual"}], "injuries": False, "safe": True,
        "rego": "ABC-123"}


@pytest.mark.asyncio
async def test_kai_hilux_claim_with_photos_to_settlement_via_trust(app_client, mongo_db):
    _, http = app_client
    db = mongo_db
    tid, ids, ckies = await _setup(db)
    r = await http.post("/api/c/claims", headers=ckies["owner"], json=FNOL)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["message"].startswith("Received — your broker will review") and j["excess"] == 850
    cid = j["claim_id"]
    up = await http.post(f"/api/c/claims/{cid}/files", headers=ckies["owner"], data={"kind": "photo", "keep_location": "false"},
                         files={"file": ("bumper.jpg", _jpeg_with_gps(), "image/jpeg")})
    assert up.status_code == 200, up.text
    c = await ck.get(db, tid, cid)
    stored = BLOBS[c["docs"][0]["storage_path"]]
    assert 0x8825 not in Image.open(io.BytesIO(stored)).getexif()  # GPS stripped
    assert not c["flags"] or all(f["code"] != "asset_not_on_schedule" for f in c["flags"])
    assert (await cert_stp.facts(db, tid, "motor"))["open_claim"] is True  # R-CoC-01 from real claim state
    kw = {"by": ids["ae"], "role": "ae"}
    c = await ck.acknowledge(db, tid, c, **kw)
    c = await ck.request_info(db, tid, c, items=["Repair quote"], due=(TODAY + timedelta(days=5)).date().isoformat(), message="", **kw)
    assert c["stage"] == "info_requested"
    req = c["info_requests"][0]["req_id"]
    a = await http.post(f"/api/c/claims/{cid}/info/{req}", headers=ckies["owner"], json={"text": "Quote attached, $2,400."})
    assert a.status_code == 200 and a.json()["claim"]["stage"] == "triage"
    c = await ck.get(db, tid, cid)
    pack = await ck.build_pack(db, tid, c, **kw)
    assert BLOBS[pack["pdf_path"]].startswith(b"%PDF") and pack["files"] == 1
    c = await ck.record_submission(db, tid, c, insurer_claim_no="FCA-778899", lodged_on=TODAY.date().isoformat(),
                                   contact="claims@fca.example", **kw)
    c = await ck.set_assessing(db, tid, c, assessor="A. Assessor", repairer="Bay Smash Repairs",
                               appointments=[{"when": "2026-11-02 10:00", "what": "Inspection"}], **kw)
    letter = await ck.add_file(db, tid, c, kind="decision_letter", data=b"%PDF-1.4 accepted", filename="letter.pdf",
                               content_type="application/pdf", keep_location=False, **kw)
    c = await ck.record_decision(db, tid, c, outcome="accepted", reason="", letter_file_id=letter["file_id"], amount="2400", **kw)
    c = await ck.settle(db, tid, c, amount="1550", excess_applied="850", payee="repairer", payee_name="Bay Smash Repairs",
                        method="trust", txn_id=None, **kw)
    async with use_tenant(tid):
        entry = await wrap(db).trust_entries.find_one({"entry_id": c["settlement"]["trust_entry_id"]})
    assert entry["kind"] == "claim_payment" and entry["amount"] == "-1550.00"
    c = await ck.close(db, tid, c, note=None, **kw)
    assert await _count(db, tid, "ClaimStageChanged") == 8 and await _count(db, tid, "ClaimLodged") == 1
    assert (await cert_stp.facts(db, tid, "motor"))["open_claim"] is False
    view = (await http.get(f"/api/c/claims/{cid}", headers=ckies["owner"])).json()["claim"]
    assert view["stage"] == "closed" and view["settlement"]["method"] == "Paid via your broker's trust account"
    assert [t["label"] for t in view["timeline"]][:3] == ["Lodged", "Triage", "Info requested"]


@pytest.mark.asyncio
async def test_asset_not_on_schedule_and_outside_period_are_flags_not_denials(app_client, mongo_db):
    _, http = app_client
    tid, ids, ckies = await _setup(mongo_db)
    chk = (await http.get("/api/c/claims/check", headers=ckies["owner"], params={"policy_id": "motor", "rego": "ZZZ-999"})).json()
    assert chk["asset_on_schedule"] is False and len(chk["vehicles"]) == 2 and chk["excess"] == 850
    body = {**FNOL, "rego": "ZZZ-999", "incident_date": (TODAY - timedelta(days=400)).date().isoformat(),
            "description": "The driver was drunk and hit a pole."}
    r = await http.post("/api/c/claims", headers=ckies["owner"], json=body)
    assert r.status_code == 200, r.text
    c = await ck.get(mongo_db, tid, r.json()["claim_id"])
    codes = {f["code"] for f in c["flags"]}
    assert {"asset_not_on_schedule", "outside_period", "late_notification", "possible_exclusion"} <= codes
    assert c["stage"] == "lodged"


@pytest.mark.asyncio
async def test_bookkeeper_403_unless_granted_and_foreign_claim_404(app_client, mongo_db):
    _, http = app_client
    tid, ids, ckies = await _setup(mongo_db)
    r = await http.post("/api/c/claims", headers=ckies["bookkeeper"], json=FNOL)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "AuthorityDenied"
    me = (await http.get("/api/c/me", headers=ckies["bookkeeper"])).json()
    mid = next(m["membership_id"] for m in me["memberships"] if m["client_id"] == "kai")
    g = await http.put(f"/api/c/members/{mid}/authority", headers=ckies["owner"], json={"switch": "can_lodge_claim", "on": True})
    assert g.status_code == 200, g.text
    assert (await http.post("/api/c/claims", headers=ckies["bookkeeper"], json=FNOL)).status_code == 200
    other = await ck.lodge(mongo_db, tid, client_id="other", policy=None, body={"description": "Other client's claim text"},
                           by="x", role="ae", channel="phone")
    assert (await http.get(f"/api/c/claims/{other['claim_id']}", headers=ckies["owner"])).status_code == 404
    assert (await http.get(f"/api/c/claims/{uuid.uuid4()}", headers=ckies["owner"])).status_code == 404
    tid2, _, ck2 = await _setup(mongo_db)
    assert (await http.get(f"/api/c/claims/{other['claim_id']}", headers=ck2["owner"])).status_code == 404


@pytest.mark.asyncio
async def test_declined_claim_shows_idr_afca_and_dispute_alerts_staff(app_client, mongo_db):
    _, http = app_client
    db = mongo_db
    tid, ids, ckies = await _setup(db)
    kw = {"by": ids["ae"], "role": "ae"}
    c = await ck.lodge(db, tid, client_id="kai", policy=None,
                       body={"claim_class": "motor", "subtype": "theft", "description": "Ute stolen from the yard overnight.", "rego": "ABC-123"},
                       by=ids["owner"], role="client", channel="in_app")
    c = await ck.acknowledge(db, tid, c, **kw)
    c = await ck.submit_mocked(db, tid, c, **kw)  # test tenant: MOCKED channel allowed
    assert c["insurer_claim"]["mocked"] and await _count(db, tid, "ClaimSubmittedToInsurer") == 1
    with pytest.raises(ck.Bad, match="decision letter"):
        await ck.record_decision(db, tid, c, outcome="declined", reason="Keys left in vehicle", letter_file_id="nope", amount=None, **kw)
    letter = await ck.add_file(db, tid, c, kind="decision_letter", data=b"%PDF-1.4 declined", filename="d.pdf",
                               content_type="application/pdf", keep_location=False, **kw)
    await ck.record_decision(db, tid, c, outcome="declined", reason="Keys left in vehicle", letter_file_id=letter["file_id"],
                             amount=None, **kw)
    view = (await http.get(f"/api/c/claims/{c['claim_id']}", headers=ckies["owner"])).json()["claim"]
    assert view["decision"]["label"] == "Declined" and "AFCA" in view["options"]["afca_text"]
    assert any("complaint" in t for t in view["options"]["text"])
    d = await http.post(f"/api/c/claims/{c['claim_id']}/dispute", headers=ckies["owner"], json={"text": "The keys were hidden."})
    assert d.status_code == 200 and d.json()["claim"]["options"]["disputed"]
    async with use_tenant(tid):
        alerts = await wrap(db).notifications.count_documents({"kind": "claim_disputed", "user_id": {"$in": [ids["ae"], ids["principal"]]}})
        complaints = await wrap(db).complaints.count_documents({})
    assert alerts == 2 and complaints == 0  # suggested to staff, never auto-created


@pytest.mark.asyncio
async def test_manual_only_refuses_mocked_insurer_channel(mongo_db):
    tid, ids, _ = await _setup(mongo_db, test=False)
    c = await ck.lodge(mongo_db, tid, client_id="kai", policy=None, body={"description": "Hail damage to the ute roof."},
                       by=ids["owner"], role="client", channel="in_app")
    c = await ck.acknowledge(mongo_db, tid, c, by=ids["ae"], role="ae")
    with pytest.raises(ck.Bad, match="manually"):
        await ck.submit_mocked(mongo_db, tid, c, by=ids["ae"], role="ae")
    assert await _count(mongo_db, tid, "ClaimSubmittedToInsurer") == 0


@pytest.mark.asyncio
async def test_sla_tick_ack_overdue_stale_and_info_reminders(mongo_db):
    db = mongo_db
    tid, ids, _ = await _setup(db)
    c = await ck.lodge(db, tid, client_id="kai", policy=None, body={"description": "Glass chipped on the highway."},
                       by=ids["owner"], role="client", channel="in_app")
    later = TODAY + timedelta(days=6)
    assert await ck.tick(db, tid, later) >= 1
    assert await ck.tick(db, tid, later) == 0  # once only
    c = await ck.get(db, tid, c["claim_id"])
    assert "ack_overdue" in c["alerts"]
    c = await ck.acknowledge(db, tid, c, by=ids["ae"], role="ae")
    await ck.request_info(db, tid, c, items=["Photos"], due=(TODAY + timedelta(days=2)).date().isoformat(), message="", by=ids["ae"],
                          role="ae")
    await ck.tick(db, tid, TODAY + timedelta(days=3))
    c = await ck.get(db, tid, c["claim_id"])
    req = c["info_requests"][0]["req_id"]
    assert {f"info_soon:{req}", f"info_due:{req}"} <= set(c["alerts"])
    async with use_tenant(tid):
        m = await ck.weekly_metrics(wrap(db), TODAY)
    assert {x["key"] for x in m} == {"claims_open", "claims_avg_days", "claims_stale"}


@pytest.mark.asyncio
async def test_broker_reply_gaw_on_g_and_principal_gate_on_p(mongo_db):
    db = mongo_db
    tid, ids, _ = await _setup(db)
    async with use_tenant(tid):
        await wrap(db).tenant_settings.update_one({}, {"$set": {"gaw_text": "TEST GAW"}}, upsert=True)
    th = await inbox.ask(db, tid, user_id=ids["owner"], client_id="kai", message="Does hail get covered?")
    ae = SimpleNamespace(tenant_id=tid, user_id=ids["ae"], active_role="ae")
    pr = SimpleNamespace(tenant_id=tid, user_id=ids["principal"], active_role="principal")
    g = await inbox.reply(db, ae, th, "Most motor policies cover hail damage, subject to the excess.", "idem-g-0001")
    assert g["advice_tier"] == "G" and g["gaw"] == "TEST GAW" and await _count(db, tid, "GeneralAdviceWarningGiven") == 1
    msgs = (await inbox.get_thread(db, tid, th))["messages"]
    assert next(m for m in msgs if m["who"] == "broker")["gaw"] == "TEST GAW"
    p_text = "Given your fleet, I recommend you increase the excess."
    with pytest.raises(HTTPException) as e:
        await inbox.reply(db, ae, th, p_text, "idem-p-0001")
    assert e.value.status_code == 409 and e.value.detail["code"] == "PersonalAdvice"
    a = await inbox.request_advice_approval(db, ae, th, p_text)
    with pytest.raises(HTTPException):
        await inbox.approve_advice(db, ae, th, a["approval_id"])  # AE can't approve
    with pytest.raises(HTTPException):
        await inbox.reply(db, ae, th, p_text, "idem-p-0002", a["approval_id"])  # not approved yet
    await inbox.approve_advice(db, pr, th, a["approval_id"])
    with pytest.raises(HTTPException):
        await inbox.reply(db, ae, th, p_text + " Edited.", "idem-p-0003", a["approval_id"])  # approval bound to the exact text
    sent = await inbox.reply(db, ae, th, p_text, "idem-p-0004", a["approval_id"])
    assert sent["advice_tier"] == "P" and await _count(db, tid, "PersonalAdviceApproved") == 1


@pytest.mark.asyncio
async def test_claim_reference_sequence_ignores_year_digits(mongo_db):
    from app.kernel import claims_scene
    tid, ids, _ = await _setup(mongo_db)
    a = await ck.lodge(mongo_db, tid, client_id="kai", policy=None, body={"description": "First claim in this tenant."},
                       by=ids["owner"], role="client", channel="in_app")
    nxt = await claims_scene.next_claim_reference(mongo_db, tid)
    y = TODAY.year
    assert a["claim_reference"] == f"CLM-{y}-000001" and nxt == f"CLM-{y}-000002"
