"""R2.6: cross-feature gaps — disabled AE, suspended tenant pause, paid-but-unaccepted lapse, claims across lapses,
complaint ↔ claim links, strict client bodies, demo reset default, sandbox duplicate GAW, Secure cookie."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.db.tenant_safe import wrap
from app.kernel import claims as ck
from app.kernel import email_channel as ec
from app.kernel import inbox, jobs, notify, platform
from app.kernel import renewals as rn
from app.kernel.tenant_ctx import use_tenant
from tests.test_r16_renewals import _people, _tenant
from tests.test_r20_acceptance import _paid_renewal, _staff, fake_storage  # noqa: F401
from tests.test_r16_quoting_mode import _real_tenant

DAY = datetime(2036, 10, 16, 10, 0, tzinfo=inbox.SYD).astimezone(timezone.utc)
TODAY = datetime.now(timezone.utc)


class Sent(list):
    def __bool__(self):  # email_channel uses `send or resend_send`
        return True

    async def __call__(self, **kw):
        self.append(kw)
        return f"msg-{len(self)}"


async def _notes(db, tid, kind):
    async with use_tenant(tid):
        return {n["user_id"] async for n in wrap(db).notifications.find({"kind": kind})}


@pytest.mark.asyncio
async def test_disabled_ae_never_counts_alerts_fall_back_and_no_email(app_client, mongo_db, monkeypatch):
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    _, http = app_client
    db = mongo_db
    tid = await _tenant(db, f"r26a-{uuid.uuid4().hex[:4]}")
    async with use_tenant(tid):
        await wrap(db).clients.insert_one({"client_id": "c1", "display_name": "Harbour Cafe"})
    ids, _ = await _people(db, tid, "c1")
    safe = wrap(db)
    async with use_tenant(tid):
        assert await notify.assigned_ae(safe, "c1") == ids["ae"]
        await safe.users.update_one({"user_id": ids["ae"]}, {"$set": {"disabled": True, "email_override": "delivered@resend.dev"}})
        assert await notify.assigned_ae(safe, "c1") is None
        assert set(await notify._client_staff(safe, "c1")) == {ids["principal"]}  # Principal + active AEs (none left)
    assert await notify.deliver(db, tid, ids["ae"], kind="x", title="t", many="t", body="b", link="/", dedupe_key="d",
                                idem="i") == "skipped:user_disabled"
    # a claim that stored the AE at lodgement re-resolves at alert time
    c = {"claim_id": "k1", "client_id": "c1", "assigned_ae": ids["ae"], "claim_reference": "CLM-T-1", "test": True}
    await ck._alert(db, tid, c, "ae", "claim_test", "Claim", "body", "claim_test:k1")
    assert await _notes(db, tid, "claim_test") == {ids["principal"]}
    # staff client page: "Assigned AE disabled — reassign"
    a = (await http.get("/api/o/clients/c1/assignment", headers=await _staff(db, tid, ids["principal"], "principal"))).json()
    assert a["assigned_disabled"] is True and a["warning"].startswith("Assigned AE disabled — reassign")
    assert ids["ae"] not in [x["user_id"] for x in a["aes"]]
    # no email to a disabled user, even for a row queued earlier
    async with use_tenant(tid):
        await safe.notifications.insert_one({"notification_id": "n-dis", "user_id": ids["ae"], "kind": "x", "title": "t", "body": "b",
                                             "link": "/", "read_at": None, "channels_state": {"email": "pending"}, "updated_at": DAY - timedelta(hours=1),
                                             "created_at": DAY})
    sent = Sent()
    assert (await ec.run_once(db, DAY, send=sent, allow=["*@resend.dev"], ids=["n-dis"])) == {"skipped:user_disabled": 1} and len(sent) == 0


@pytest.mark.asyncio
async def test_suspended_tenant_pauses_background_work_and_holds_lapses(mongo_db, monkeypatch):
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    db = mongo_db
    tid = await _tenant(db, f"r26s-{uuid.uuid4().hex[:4]}")
    exp = TODAY - timedelta(days=1)
    async with use_tenant(tid):
        await wrap(db).clients.insert_one({"client_id": "c1", "display_name": "Harbour Cafe"})
        await wrap(db).policies.insert_one({"policy_id": "p1", "client_id": "c1", "status": "managed", "product": "Business Pack",
                                            "insurer_display": "Fictional Carrier A", "inception": exp - timedelta(days=365), "expiry": exp,
                                            "premium_amount": 900, "schedule": {"excess": {"any_one_occurrence": 250}}})
    ids, _ = await _people(db, tid, "c1")
    r = await rn.start(db, tid, await rn._policy(db, tid, "p1"), now=exp - timedelta(days=60))
    async with use_tenant(tid):
        await wrap(db).users.update_one({"user_id": ids["principal"]}, {"$set": {"email_override": "delivered@resend.dev"}})
        await wrap(db).notifications.insert_one({"notification_id": "n-s", "user_id": ids["principal"], "kind": "x", "title": "Hello",
                                                 "body": "b", "link": "/o", "read_at": None, "channels_state": {"email": "pending"},
                                                 "updated_at": DAY - timedelta(hours=1), "created_at": DAY})
    await platform.set_status(db, tid, "suspended", "test", "padmin")
    # the scheduler queues no per-tenant work for a suspended tenant
    await db.jobs.delete_many({"tenant_id": tid})
    await jobs.scheduler_tick(db, f"t-{uuid.uuid4().hex[:4]}", TODAY + timedelta(days=400))
    assert await db.jobs.count_documents({"tenant_id": tid}) == 0
    # queued email waits (held: tenant_suspended)
    sent = Sent()
    assert await ec.run_once(db, DAY, send=sent, allow=["*@resend.dev"], ids=["n-s"], ignore_quiet=True) == {} and len(sent) == 0
    n = await db.notifications.find_one({"notification_id": "n-s"})
    assert n["channels_state"]["email"] == "held" and n["hold_reason"] == "tenant_suspended"
    # a renewal tick queued before the suspension does nothing
    from app.kernel.job_handlers import _renewals_tick
    await _renewals_tick(db, {"payload": {"tenant_id": tid}})
    assert (await rn.get(db, tid, r["renewal_id"]))["stage"] != "lapsed"
    # reactivation: past-due renewals are listed for the Operator, not auto-lapsed; email resumes
    await platform.set_status(db, tid, "live", "back", "padmin", force=True)
    assert (await rn.get(db, tid, r["renewal_id"]))["lapse_hold"] is True
    assert (await rn.tick(db, tid, TODAY, ["p1"]))["lapsed"] == 0
    assert [x["renewal_id"] for x in await rn.past_due_review(db, tid)] == [r["renewal_id"]]
    assert {ids["principal"]} <= await _notes(db, tid, "renewal_past_due_review")
    assert (await ec.run_once(db, DAY, send=sent, allow=["*@resend.dev"], ids=["n-s"], ignore_quiet=True)) == {"sent": 1}
    r = await rn.confirm_lapse(db, tid, await rn.get(db, tid, r["renewal_id"]), by=ids["principal"], role="principal")
    assert r["stage"] == "lapsed" and not r["lapse_hold"]


@pytest.mark.asyncio
async def test_paid_unaccepted_alert_refund_or_bind_and_late_principal_signature(app_client, mongo_db):
    _, http = app_client
    db = mongo_db
    tid, exp = await _real_tenant(db)
    ids, ck_ = await _people(db, tid, "c1")
    r = await _paid_renewal(db, http, tid, ids, ck_, exp)
    await rn.tick(db, tid, exp - timedelta(days=7), ["p1"])
    assert {ids["ae"], ids["principal"]} <= await _notes(db, tid, "renewal_paid_unaccepted")
    async with use_tenant(tid):
        n = await wrap(db).notifications.find_one({"kind": "renewal_paid_unaccepted", "user_id": ids["ae"]})
    assert "held in trust" in n["body"] and "acceptance" in n["body"].lower()
    assert (await rn.tick(db, tid, exp + timedelta(hours=1), ["p1"]))["lapsed"] == 1
    r = await rn.get(db, tid, r["renewal_id"])
    assert r["stage"] == "lapsed" and r["refund_review"]["state"] == "open"
    async with use_tenant(tid):
        te = await wrap(db).trust_entries.find_one({"payable_id": r["payable_id"], "kind": "receipt"})
    assert te["refund_review"] is True
    assert {ids["ae"], ids["principal"]} <= await _notes(db, tid, "renewal_refund_or_bind")
    url = f"/api/o/acceptance/renewal/{r['renewal_id']}/offline"
    scan = {"file": ("late.pdf", b"%PDF-1.4\n%late wet signature", "application/pdf")}
    assert (await http.post(url, headers=await _staff(db, tid, ids["ae"]), data={"reason": "late wet signature"}, files=scan)).status_code == 403
    ok = await http.post(url, headers=await _staff(db, tid, ids["principal"], "principal"), data={"reason": "Client signed late"}, files=scan)
    assert ok.status_code == 200, ok.text
    assert ok.json()["stage"] == "bound"
    r = await rn.get(db, tid, r["renewal_id"])
    assert r["cover_continuity"]["backdated"] is True and r["refund_review"]["state"] == "bound" and r["signature"]["late"] is True
    pk = rn.packet(r, await rn._policy(db, tid, "p1"), await rn.payable(db, tid, r))
    assert any(ln["label"].startswith("Cover continuity") for ln in pk["lines"])


@pytest.mark.asyncio
async def test_mark_refund_on_lapsed_paid_renewal(app_client, mongo_db):
    _, http = app_client
    db = mongo_db
    tid, exp = await _real_tenant(db)
    ids, ck_ = await _people(db, tid, "c1")
    r = await _paid_renewal(db, http, tid, ids, ck_, exp)
    await rn.tick(db, tid, exp + timedelta(hours=1), ["p1"])
    d = await http.post(f"/api/o/renewals/{r['renewal_id']}/mark-refund", headers=await _staff(db, tid, ids["ae"]), json={})
    assert d.status_code == 200 and d.json()["renewal"]["refund_review"]["state"] == "refund"


@pytest.mark.asyncio
async def test_claim_after_lapse_or_renewal_checks_the_term_in_force(app_client, mongo_db):
    _, http = app_client
    db = mongo_db
    tid = await _tenant(db, f"r26c-{uuid.uuid4().hex[:4]}")
    old_start, old_end = TODAY - timedelta(days=400), TODAY - timedelta(days=35)
    async with use_tenant(tid):
        await wrap(db).clients.insert_one({"client_id": "c1", "display_name": "Harbour Cafe"})
        for pid, st, start, end, extra in (("lap", "lapsed", old_start, old_end, {}),
                                            ("old", "renewed", old_start, old_end, {"renewed_to": "new"}),
                                            ("new", "managed", old_end, old_end + timedelta(days=365), {})):
            await wrap(db).policies.insert_one({"policy_id": pid, "client_id": "c1", "status": st, "product": "Business Pack",
                                                "insurer_display": "Fictional Carrier A", "inception": start, "expiry": end,
                                                "schedule": {"excess": {"any_one_occurrence": 250}}, **extra})
    ids, cookies = await _people(db, tid, "c1")
    inc = (old_end - timedelta(days=10)).date().isoformat()
    body = {"claim_class": "property", "subtype": "damage", "incident_date": inc, "description": "Storm damaged the awning badly."}
    for pid in ("lap", "new"):
        p = await rn._policy(db, tid, pid)
        c = await ck.lodge(db, tid, client_id="c1", policy=p, body=body, by=ids["owner"], role="client", channel="in_app")
        assert "outside_period" not in [f["code"] for f in c["flags"]], pid
    st = await ck.policy_status(db, tid, "lap")
    assert st["label"] == "Lapsed" and "lapsed" in st["text"].lower()
    v = (await http.get(f"/api/c/claims/{c['claim_id']}", headers=cookies["owner"])).json()["claim"]
    assert v["policy_status"]["label"] == "In force"
    p = await rn._policy(db, tid, "new")
    late = await ck.lodge(db, tid, client_id="c1", policy=p, body={**body, "incident_date": (old_start - timedelta(days=5)).date().isoformat()},
                          by=ids["owner"], role="client", channel="in_app")
    assert "outside_period" in [f["code"] for f in late["flags"]]  # genuinely before any term


@pytest.mark.asyncio
async def test_complaint_links_to_declined_claim(app_client, mongo_db):
    from app.kernel import complaints as cm
    _, http = app_client
    db = mongo_db
    tid = await _tenant(db, f"r26k-{uuid.uuid4().hex[:4]}")
    async with use_tenant(tid):
        await wrap(db).clients.insert_one({"client_id": "c1", "display_name": "Harbour Cafe"})
        await wrap(db).clients.insert_one({"client_id": "c2", "display_name": "Other"})
    ids, _ = await _people(db, tid, "c1")
    c = await ck.lodge(db, tid, client_id="c1", policy=None, body={"claim_class": "other", "description": "Something broke at the shop."},
                       by=ids["ae"], role="ae", channel="phone")
    h = await _staff(db, tid, ids["ae"])
    url = f"/api/o/claims/{c['claim_id']}/complaint"
    assert (await http.post(url, headers=h, json={})).status_code == 409  # no decline yet
    async with use_tenant(tid):
        await wrap(db).claims.update_one({"claim_id": c["claim_id"]}, {"$set": {"stage": "decision", "decision": {
            "outcome": "declined", "reason": "excluded", "disputed": {"reason": "I disagree with the exclusion"}}}})
    d = await http.post(url, headers=h, json={})
    assert d.status_code == 200, d.text
    links = d.json()["complaints"]
    assert len(links) == 1 and links[0]["stage"] == "received"
    async with use_tenant(tid):
        m = await wrap(db).complaints.find_one({"links.claim_id": c["claim_id"]})
    assert m["source"] == "claim" and m["category"] == "claims" and m["details"] == "I disagree with the exclusion"
    assert (await http.post(url, headers=h, json={})).status_code == 409  # already linked and open
    with pytest.raises(cm.Bad):
        await cm.create(db, tid, "complaint", client_id="c2", category="claims", summary="x", details="", source="call",
                        claim_id=c["claim_id"], by=ids["ae"], role="ae")
    r = await http.post("/api/o/compliance/complaints", headers=h, json={"client_id": "c1", "category": "claims", "summary": "Via form",
                                                                          "source": "claim", "claim_id": c["claim_id"]})
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_client_write_bodies_are_strict_and_motor_needs_rego(app_client, mongo_db):
    from tests.test_r18_claims import FNOL, _setup
    _, http = app_client
    tid, ids, cookies = await _setup(mongo_db)
    bad = await http.post("/api/c/claims", headers=cookies["owner"], json={**{k: v for k, v in FNOL.items() if k != "rego"}, "vehicle_rego": "ABC-123"})
    assert bad.status_code == 422
    no_rego = await http.post("/api/c/claims", headers=cookies["owner"], json={k: v for k, v in FNOL.items() if k != "rego"})
    assert no_rego.status_code == 409 and "rego" in no_rego.json()["detail"]
    assert (await http.post("/api/c/claims", headers=cookies["owner"], json=FNOL)).status_code == 200
    assert (await http.put("/api/notifications/prefs", headers=cookies["owner"], json={"email": True, "sneaky": 1})).status_code == 422


def test_demo_reset_defaults_to_operator():
    s = (Path(__file__).resolve().parents[2] / "scripts" / "demo_reset.sh").read_text()
    assert 'EMAIL="${2:-operator@harbourline.example}"' in s


@pytest.mark.asyncio
async def test_sandbox_reset_reuses_doc_per_kind_and_archives_duplicates(app_client, mongo_db):
    from app.kernel import compliance_library as lib
    from app.kernel import sandbox
    from tests.test_r23_business_details import GOOD
    from tests.test_r24_sandbox_reset import _pip
    _, http = app_client
    db = mongo_db
    tid = (await sandbox.ensure(db))["tenant_id"]
    pip = await _pip(db, tid)
    assert (await http.put("/api/o/compliance/business-details", headers=pip, json=GOOD)).status_code == 200
    docs = {d["kind"]: d for d in await lib.listing(db, tid) if d.get("doc_id")}
    await lib.approve(db, tid, docs["GAW"]["doc_id"], reviewer_name="Rita", confirmed=True, by="p", role="principal")
    async with use_tenant(tid):  # the R2.4 bug left a second GAW behind
        dup = {**(await wrap(db).compliance_library.find_one({"doc_id": docs["GAW"]["doc_id"]}, {"_id": 0})),
               "doc_id": str(uuid.uuid4()), "status": "draft", "created_at": datetime.now(timezone.utc)}
        await wrap(db).compliance_library.insert_one(dup)
    c = (await http.post("/api/o/compliance/sandbox/reset", headers=pip)).json()
    assert c["created"] == 0 and c["archived_duplicates"] == 1 and c["withdrawn"] == 1
    after = [d for d in await lib.listing(db, tid) if d.get("doc_id")]
    kinds = [d["kind"] for d in after]
    assert len(kinds) == len(set(kinds)) == 5
    gaw = next(d for d in after if d["kind"] == "GAW")
    assert gaw["doc_id"] == docs["GAW"]["doc_id"] and gaw["status"] == "draft"  # same document, fresh blank draft
    async with use_tenant(tid):
        assert await wrap(db).events.count_documents({"type": "ComplianceDocArchived", "payload.doc_id": dup["doc_id"]}) == 1
    again = (await http.post("/api/o/compliance/sandbox/reset", headers=pip)).json()
    assert again == {"details_cleared": False, "withdrawn": 0, "redrafted": 0, "created": 0}


def test_session_cookie_is_secure_over_https_and_in_production(monkeypatch):
    from app.kernel import auth_flows as af
    monkeypatch.setenv("APP_BASE_URL", "https://app.brokaid.example")
    assert af.cookie_flags()["secure"] is True and af.cookie_flags()["httponly"] is True
    monkeypatch.setenv("APP_BASE_URL", "http://localhost:3000")
    monkeypatch.setattr(af, "is_production", lambda: True)
    assert af.cookie_flags()["secure"] is True
