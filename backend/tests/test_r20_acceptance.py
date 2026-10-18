"""R2.0 — click-to-accept + signed offline, in a manual_only (BurMac-style) tenant."""
from __future__ import annotations

import hashlib
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.auth.dispatch import create_session_scoped
from app.auth.sessions import COOKIE_NAME
from app.db.tenant_safe import wrap
from app.kernel import acceptance as acc
from app.kernel import bankfeed, holidays, renewals as rn
from app.kernel.agents.placement import PLACEMENT_ELIGIBLE_CLASSES
from app.kernel.tenant_ctx import use_tenant
from app.routes import c_scene
from tests.test_r16_quoting_mode import _real_tenant
from tests.test_r16_renewals import _people


@pytest.fixture(autouse=True)
def fake_storage(monkeypatch):
    blobs: dict[str, tuple[bytes, str]] = {}

    async def put(path, data, ct):
        blobs[path] = (data, ct)
        return {"path": path}

    async def get(path):
        return blobs[path]
    from app.kernel import object_store
    monkeypatch.setattr(object_store, "put_object", put)
    monkeypatch.setattr(object_store, "get_object", get)
    return blobs


def test_name_match():
    assert acc.name_match("Owner R16", "Owner R16") == "exact"
    assert acc.name_match("owner  r16.", "Owner R16") == "exact"
    assert acc.name_match("Kai J Nguyen", "Kai Nguyen") == "close"
    assert acc.name_match("Kai Nguyem", "Kai Nguyen") == "close"
    assert acc.name_match("Someone Else", "Kai Nguyen") == "mismatch"


async def _staff(db, tid, uid, role="ae"):
    s = await create_session_scoped(db, tid, user_id=uid, brokerage_id="b", active_role=role, mfa_verified=True,
                                    ttl=timedelta(minutes=10))
    return {"Cookie": f"{COOKIE_NAME}={s.session_id}"}


async def _manual_endorsement(db, http, tid, ids, ck) -> str:
    env = str(uuid.uuid4())
    policy = await rn._policy(db, tid, "p1")
    change = [{"field_path": "schedule.vehicles[]", "operation": "add", "to_value": {"rego": "XYZ-789"}}]
    state = {"intake": {"payload": {"proposed_changes": change}, "class_key": sorted(PLACEMENT_ELIGIBLE_CLASSES)[0]}, "gates": "ok",
             "placement": {"structured_submission": {"requested_changes": change}}, "placement_gate": "ok", "submission": True}
    from app.kernel import scene
    await scene.emit_client_change_requested(db, tenant_id=tid, user_id=ids["owner"], envelope_id=env, policy_id="p1",
                                             client_id="c1", raw_message="Add XYZ-789")
    await c_scene._run_intake_placement_async(db, tenant_id=tid, user_id=ids["owner"], envelope_id_str=env, policy_id="p1",
                                              policy=policy, raw_message="Add XYZ-789", state=state)
    h = await _staff(db, tid, ids["ae"])
    assert (await http.post(f"/api/o/queue/{env}/insurer-quote", headers=h,
                            json={"premium_change_amount": "92.40", "insurer": "Real Insurer", "reference": "RI-55"})).status_code == 200
    assert (await http.post(f"/api/o/queue/{env}/approve", headers=h, json={"reason": "real quote"})).status_code == 200
    assert (await http.post(f"/api/c/matters/p1/approve/{env}", headers=ck["owner"])).status_code == 200
    r = await http.post(f"/api/c/matters/p1/paid/{env}", headers=ck["owner"], json={})
    assert r.status_code == 200, r.text
    return env


@pytest.mark.asyncio
async def test_manual_only_endorsement_click_to_accept_to_certificate(app_client, mongo_db):
    _, http = app_client
    db = mongo_db
    tid, _ = await _real_tenant(db)
    assert await holidays.quoting_mode(db, tid) == "manual_only"
    ids, ck = await _people(db, tid, "c1")
    env = await _manual_endorsement(db, http, tid, ids, ck)
    assert (await http.post(f"/api/c/matters/p1/sign/{env}", headers=ck["owner"])).status_code == 409  # mocked e-sign stays off
    base = f"/api/c/acceptance/endorsement/{env}"
    resp = await http.get(base, headers=ck["owner"])
    assert resp.status_code == 200, resp.text
    v = resp.json()
    assert v["ready"], v
    assert v["label"] == "Accepted in brokAId (not a digital signature)" and len(v["doc_sha256"]) == 64
    assert any("92.40" in str(line) for line in v["lines"]) and any("Real Insurer" in str(line) for line in v["lines"])
    pdf = await http.get(f"{base}/pdf", headers=ck["owner"])
    assert pdf.content[:4] == b"%PDF" and hashlib.sha256(pdf.content).hexdigest() == v["doc_sha256"]
    body = {"typed_name": "Owner R16", "accepted": True, "doc_sha256": v["doc_sha256"]}
    # bookkeeper: no can_sign → 403; tampered hash → 409; mismatched name → prompt; unticked → 422
    assert (await http.post(base, headers=ck["bookkeeper"], json=body)).status_code == 403
    assert (await http.post(base, headers=ck["owner"], json={**body, "doc_sha256": "0" * 64})).status_code == 409
    mm = await http.post(base, headers=ck["owner"], json={**body, "typed_name": "Totally Different"})
    assert mm.status_code == 409 and mm.json()["detail"]["code"] == "name_mismatch"
    assert (await http.post(base, headers=ck["owner"], json={**body, "accepted": False})).status_code == 422
    ok = await http.post(base, headers={**ck["owner"], "User-Agent": "pytest-r20"}, json=body)
    assert ok.status_code == 200, ok.text
    assert ok.json()["state"] == "certificate_ready" and ok.json()["accepted"]["method"] == "click_to_accept"
    async with use_tenant(tid):
        safe = wrap(db)
        ev = await safe.events.find_one({"type": "ClientAcceptanceRecorded", "payload.envelope_id": env})
        cert = await safe.events.find_one({"type": "CertificateRegenerated", "payload.envelope_id": env})
        note = await safe.notifications.find_one({"user_id": ids["owner"], "kind": "acceptance_receipt"})
        assert await safe.events.count_documents({"payload.envelope_id": env, "type": {"$in": ["SignatureRequested", "SignatureCompleted"]}}) == 0
    p = ev["payload"]
    assert ev["mocked"] is False and p["user_id"] == ids["owner"] and p["typed_name"] == "Owner R16" and p["name_match"] == "exact"
    assert p["doc_sha256"] == v["doc_sha256"] and p["session_id"] and p["user_agent"] == "pytest-r20" and "ip" in p
    assert p["accepted_at_utc"].endswith("+00:00") and p["accepted_at_sydney"][-6:] in ("+10:00", "+11:00")
    assert p["membership"]["role"] == "owner" and p["membership"]["authority"]["can_sign"] is True
    assert cert["payload"]["certificate"]["acceptance"]["event_id"] == ev["event_id"]
    assert note and v["doc_sha256"][:16] in note["body"]
    pk = await http.get(f"/api/o/envelope/{env}/change-packet", headers=await _staff(db, tid, ids["ae"]))
    assert pk.status_code == 200 and "Accepted in brokAId (not a digital signature)" in pk.text and v["doc_sha256"] in pk.text
    again = await http.post(base, headers=ck["owner"], json=body)  # idempotent
    assert again.status_code == 200 and again.json()["accepted"]["event_id"] == ev["event_id"]


@pytest.mark.asyncio
async def test_foreign_envelope_is_404(app_client, mongo_db):
    _, http = app_client
    db = mongo_db
    tid, _ = await _real_tenant(db)
    ids, ck = await _people(db, tid, "c1")
    env = await _manual_endorsement(db, http, tid, ids, ck)
    tid2, _ = await _real_tenant(db)
    _, ck2 = await _people(db, tid2, "c1")
    for m, url in (("get", f"/api/c/acceptance/endorsement/{env}"), ("get", f"/api/c/acceptance/endorsement/{env}/pdf"),
                   ("post", f"/api/c/acceptance/endorsement/{env}")):
        kw = {"json": {"typed_name": "x", "accepted": True, "doc_sha256": "0" * 64}} if m == "post" else {}
        assert (await getattr(http, m)(url, headers=ck2["owner"], **kw)).status_code == 404, url
    assert (await http.get(f"/api/c/acceptance/renewal/{uuid.uuid4()}", headers=ck["owner"])).status_code == 404


async def _paid_renewal(db, http, tid, ids, ck, exp):
    r = await rn.start(db, tid, await rn._policy(db, tid, "p1"), now=exp - timedelta(days=60))
    kw = {"by": ids["ae"], "role": "ae"}
    r = await rn.accept_info(db, tid, r, "Confirmed by phone", **kw)
    r = await rn.manual_quote(db, tid, r, {"insurer": "Real Insurer", "premium": "1050", "excess": "500"}, by_name="AE", **kw)
    r = await rn.recommend(db, tid, r, r["quotes"][0]["quote_id"], "Same cover and excess, small increase.", ["Same cover"], **kw)
    r = await rn.send_to_client(db, tid, r, **kw)
    d = (await http.post(f"/api/c/renewals/{r['renewal_id']}/decide", headers=ck["owner"], json={"choice": r["quotes"][0]["quote_id"]})).json()
    line = [{"date": date(2026, 10, 2), "amount": Decimal("1050.00"), "description": f"BPAY {d['payment']['crn']} RENEWAL",
             "balance": Decimal("1050.00")}]
    from app.scripts import bank_synth
    await bankfeed.import_statement(db, tid, data=bank_synth.cba(line).encode(), filename="r20.csv", user_id=ids["ae"], storage_path=None)
    r = await rn.get(db, tid, r["renewal_id"])
    assert r["stage"] == "paid"
    return r


@pytest.mark.asyncio
async def test_manual_only_renewal_click_to_accept_reaches_bound(app_client, mongo_db):
    _, http = app_client
    db = mongo_db
    tid, exp = await _real_tenant(db)
    ids, ck = await _people(db, tid, "c1")
    r = await _paid_renewal(db, http, tid, ids, ck, exp)
    view = (await http.get(f"/api/c/renewals/{r['renewal_id']}", headers=ck["owner"])).json()
    assert view["esign_available"] is False and view["acceptance_available"] is True
    base = f"/api/c/acceptance/renewal/{r['renewal_id']}"
    v = (await http.get(base, headers=ck["owner"])).json()
    text = str(v["lines"])
    assert "Real Insurer" in text and "A$1,050.00" in text and "A$500.00" in text and "Period of cover" in text
    body = {"typed_name": "owner r16", "accepted": True, "doc_sha256": v["doc_sha256"]}
    assert (await http.post(base, headers=ck["bookkeeper"], json=body)).status_code == 403
    assert (await http.post(base, headers=ck["owner"], json={**body, "doc_sha256": "f" * 64})).status_code == 409
    ok = await http.post(base, headers=ck["owner"], json=body)
    assert ok.status_code == 200, ok.text
    assert ok.json()["stage"] == "bound" and ok.json()["new_policy_id"] and ok.json()["verifier_token"]
    r = await rn.get(db, tid, r["renewal_id"])
    assert r["signature"]["method"] == "click_to_accept" and r["signature"]["mocked"] is False
    packet = rn.packet(r, await rn._policy(db, tid, "p1"), await rn.payable(db, tid, r))
    assert "Accepted in brokAId (not a digital signature)" in packet["text"] and v["doc_sha256"] in packet["text"]
    async with use_tenant(tid):
        mocked = [e["type"] async for e in wrap(db).events.find({"payload.mocked": True})]
        assert mocked == [], mocked


@pytest.mark.asyncio
async def test_signed_offline_scan_is_magic_byte_checked_and_binds(app_client, mongo_db):
    _, http = app_client
    db = mongo_db
    tid, exp = await _real_tenant(db)
    ids, ck = await _people(db, tid, "c1")
    r = await _paid_renewal(db, http, tid, ids, ck, exp)
    h = await _staff(db, tid, ids["ae"])
    url = f"/api/o/acceptance/renewal/{r['renewal_id']}/offline"
    fake = await http.post(url, headers=h, data={"reason": "Insurer needs wet signature"},
                           files={"file": ("signed.pdf", b"MZ\x90\x00 not a pdf", "application/pdf")})
    assert fake.status_code == 422
    assert (await http.post(url, headers=h, data={"reason": " "}, files={"file": ("s.pdf", b"%PDF-1.4 x", "application/pdf")})).status_code == 422
    assert (await http.post(url, headers=ck["owner"], data={"reason": "x"},
                            files={"file": ("s.pdf", b"%PDF-1.4 x", "application/pdf")})).status_code in (401, 403)
    ok = await http.post(url, headers=h, data={"reason": "Insurer needs wet signature"},
                         files={"file": ("signed.pdf", b"%PDF-1.4\n%signed scan", "application/pdf")})
    assert ok.status_code == 200, ok.text
    assert ok.json()["stage"] == "bound" and ok.json()["accepted"]["method"] == "offline"
    async with use_tenant(tid):
        ev = await wrap(db).events.find_one({"type": "OfflineSignatureRecorded", "payload.renewal_id": r["renewal_id"]}) or \
            await wrap(db).events.find_one({"type": "OfflineSignatureRecorded", "payload.ref_id": r["renewal_id"]})
    assert ev["payload"]["reason"] == "Insurer needs wet signature" and ev["payload"]["scan_type"] == "application/pdf"
    assert ev["payload"]["scan_sha256"] == hashlib.sha256(b"%PDF-1.4\n%signed scan").hexdigest()
