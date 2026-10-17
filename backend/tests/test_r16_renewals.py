"""R1.6 — renewal pipeline (injected clock), holiday calendar, advice gate, lapse → EXPIRED, bookkeeper 403, isolation."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.auth.dispatch import create_session_scoped
from app.auth.sessions import COOKIE_NAME
from app.db.tenant_safe import wrap
from app.kernel import bankfeed, holidays, inbox, renewals as rn, trail_mongo
from app.kernel.authority import ROLE_AUTHORITY
from app.kernel.tenant_ctx import use_tenant
from app.scripts import bank_synth

SYD = holidays.SYD
KAI_MOTOR = "00000000-0000-4000-8000-000000000104"


def test_holiday_calendar_applies_to_business_day_rules():
    xmas_eve = datetime(2026, 12, 24, 10, tzinfo=SYD)
    assert inbox.reply_by_for(xmas_eve) == datetime(2026, 12, 29, 10, tzinfo=SYD)  # Fri 25 Xmas, Mon 28 Boxing (observed)
    assert inbox.reply_by_for(datetime(2026, 10, 2, 9, tzinfo=SYD)) == datetime(2026, 10, 6, 9, tzinfo=SYD)  # NSW Labour Day
    assert inbox.reply_by_for(datetime(2026, 10, 2, 9, tzinfo=SYD), "VIC") == datetime(2026, 10, 5, 9, tzinfo=SYD)
    assert not holidays.is_business_day(date(2026, 11, 3), "VIC") and holidays.is_business_day(date(2026, 11, 3), "NSW")
    assert holidays.business_days_between(date(2026, 12, 24), date(2027, 1, 4)) == 4  # 29, 30, 31 Dec + 4 Jan (1 Jan holiday)
    assert bankfeed.business_days_between(date(2026, 12, 24), date(2027, 1, 4)) == 4
    assert holidays.on_or_before(date(2027, 1, 1)) == date(2026, 12, 31)
    assert holidays.on_or_before(date(2028, 1, 3)) == date(2027, 12, 31)
    for y in holidays.YEARS:
        assert holiday_count(y) >= 8  # weekday NSW holidays (Anzac Day 2026/2027 falls on a weekend)


def holiday_count(y):
    return sum(1 for d in list(holidays.NATIONAL) + list(holidays.STATE["NSW"]) if d.year == y)


async def _tenant(db, slug, test=True):
    tid = str(uuid.uuid4())
    async with use_tenant(tid):
        await trail_mongo.create_tenant(wrap(db), slug=slug, display_name=slug, tenant_id_override=tid)
    if test:  # test tenants may use the MOCKED channel (R1.6 quoting_mode)
        await db.tenants.update_one({"tenant_id": tid}, {"$set": {"test": True}})  # tenant-safe: system-only
    return tid


async def _people(db, tid, client_id):
    """Owner, bookkeeper (client) and AE, Principal (staff) for one client in one tenant."""
    safe, ids = wrap(db), {}
    async with use_tenant(tid):
        for key, kind in (("owner", "client"), ("bookkeeper", "client"), ("ae", "operator"), ("principal", "operator")):
            uid = str(uuid.uuid4())
            await safe.users.insert_one({"user_id": uid, "email": f"{key}-{uid[:6]}@r16.example", "display_name": f"{key.title()} R16",
                                         "kind": kind})
            ids[key] = uid
        for key, role in (("owner", "owner"), ("bookkeeper", "bookkeeper")):
            await safe.user_client_links.insert_one({"user_id": ids[key], "tenant_id": tid, "client_id": client_id,
                                                     "role": "client_owner" if key == "owner" else "client_contact"})
            await safe.memberships.insert_one({"membership_id": str(uuid.uuid4()), "client_id": client_id, "user_id": ids[key],
                                               "role": role, "authority": ROLE_AUTHORITY[role], "revoked_at": None})
        for key in ("ae", "principal"):
            await safe.user_brokerage_roles.insert_one({"user_id": ids[key], "tenant_id": tid, "brokerage_id": "b", "role": key,
                                                        "active": True})
        await safe.clients.update_one({"client_id": client_id}, {"$set": {"assigned_ae_user_id": ids["ae"]}})
    cookies = {}
    for key, role in (("owner", "client_owner"), ("bookkeeper", "client_contact")):
        s = await create_session_scoped(db, tid, user_id=ids[key], client_id=client_id, active_role=role, mfa_verified=True,
                                        ttl=timedelta(minutes=30))
        cookies[key] = {"Cookie": f"{COOKIE_NAME}={s.session_id}"}
    return ids, cookies


async def _count(db, tid, typ):
    async with use_tenant(tid):
        return await wrap(db).events.count_documents({"type": typ})


async def _full_renewal(db, http, tid, policy_id, ids, ck, answers, insurer_names):
    pol = await rn._policy(db, tid, policy_id)
    exp = rn._utc(pol["expiry"])
    t0 = exp - timedelta(days=60)
    assert (await rn.tick(db, tid, t0 - timedelta(days=1), [policy_id]))["started"] == 0  # T-61: not yet
    assert (await rn.tick(db, tid, t0, [policy_id]))["started"] == 1
    async with use_tenant(tid):
        r = await wrap(db).renewals.find_one({"policy_id": policy_id}, {"_id": 0})
    assert r["stage"] == "upcoming" and rn._utc(r["started_at"]) == t0
    kw = {"by": ids["ae"], "role": "ae"}
    r = await rn.request_info(db, tid, r, now=t0, **kw)
    base = f"/api/c/renewals/{r['renewal_id']}"
    view = (await http.get(base, headers=ck["owner"])).json()
    assert view["stage"] == "info_requested" and view["checklist"]
    resp = await http.post(f"{base}/info", headers=ck["owner"], json={"answers": answers})
    assert resp.status_code == 200, resp.text
    r = await rn.get(db, tid, r["renewal_id"])
    assert r["stage"] == "info_received" and not r["needs_review"], r["issues"]
    r = await rn.request_quotes(db, tid, r, insurer_names, now=t0, **kw)
    r = await rn.manual_quote(db, tid, r, {"insurer": "Offline Mutual", "premium": "1234.50",
                                           "excess": str(pol["schedule"]["excess"]["any_one_occurrence"]),
                                           "key_terms": ["Same cover as expiring"]}, by_name="AE R16", now=t0, **kw)
    assert r["stage"] == "quotes_in" and len(r["quotes"]) == 4
    assert [q["data_basis"] for q in r["quotes"]] == ["MOCKED"] * 3 + ["manual entry by AE R16"]
    for q in r["quotes"][:3]:
        assert abs(Decimal(q["premium"]) / Decimal(r["expiring_premium"]) - 1) <= Decimal("0.15")
    manual = r["quotes"][3]["quote_id"]
    r = await rn.recommend(db, tid, r, manual, "Offline Mutual is the lowest premium with the same cover and excess as expiring.",
                           ["Lowest premium", "Same limits and excess"], now=t0, **kw)
    assert r["recommendation"]["tier"] == "G" and r["stage"] == "recommendation_ready"
    assert (await http.get(base, headers=ck["owner"])).json().get("options") is None  # not released yet
    r = await rn.send_to_client(db, tid, r, now=t0, **kw)
    view = (await http.get(base, headers=ck["owner"])).json()
    assert view["stage"] == "client_decision" and len(view["options"]) == 4 and view["recommendation"]["general_advice_warning"]
    assert (await http.post(f"{base}/decide", headers=ck["bookkeeper"], json={"choice": manual})).status_code == 403
    d = await http.post(f"{base}/decide", headers=ck["owner"], json={"choice": manual})
    assert d.status_code == 200 and d.json()["payment"]["crn"]
    crn = d.json()["payment"]["crn"]
    assert (await http.post(f"{base}/sign", headers=ck["owner"])).status_code == 409  # not paid yet
    p = await http.post(f"{base}/paid", headers=ck["owner"])
    assert p.json()["payment"]["state"] == "advised" and p.json()["stage"] == "client_decision"
    received_before = await _count(db, tid, "PremiumReceived")
    line = [{"date": date(2026, 10, 2), "amount": Decimal("1234.50"), "description": f"BPAY {crn} RENEWAL", "balance": Decimal("1234.50")}]
    await bankfeed.import_statement(db, tid, data=bank_synth.cba(line).encode(), filename=f"{policy_id[:6]}.csv", user_id=ids["ae"],
                                    storage_path=None)
    assert await _count(db, tid, "PremiumReceived") == received_before + 1
    assert (await rn.get(db, tid, r["renewal_id"]))["stage"] == "paid"
    s = await http.post(f"{base}/sign", headers=ck["owner"])
    assert s.status_code == 200, s.text
    assert s.json()["stage"] == "bound" and s.json()["new_policy_id"]
    v = await http.get(f"/api/verifier/{s.json()['verifier_token']}")
    assert v.status_code == 200 and v.json()["verdict"] == "VALID"
    new = await rn._policy(db, tid, s.json()["new_policy_id"])
    assert rn._utc(new["inception"]) == exp and rn._utc(new["expiry"]).year == exp.year + 1
    assert new["insurer_display"] == "Offline Mutual" and new["premium_amount"] == 1234.5
    assert datetime.fromisoformat(str(v.json()["effective_from"])).date() == exp.date()
    assert (await rn._policy(db, tid, policy_id))["status"] == "renewed"
    r = await rn.get(db, tid, r["renewal_id"])
    assert [h["to"] for h in r["history"]] == ["info_requested", "info_received", "market", "quotes_in", "recommendation_ready",
                                               "client_decision", "paid", "signed", "bound"]
    assert await _count(db, tid, "RenewalStageChanged") >= 9 and await _count(db, tid, "RenewalChangePacketIssued") >= 1
    return r, new


@pytest.mark.asyncio
async def test_full_renewal_on_harbourline_demo_policy(seeded, app_client, mongo_db):
    _, http = app_client
    db = mongo_db
    pol = await db.policies.find_one({"policy_id": KAI_MOTOR})  # tenant-safe: system-only — locate the seeded demo tenant
    tid = pol["tenant_id"]
    ids, ck = await _people(db, tid, pol["client_id"])
    names = (await rn.insurers(db, tid))[:3]
    while len(names) < 3:
        async with use_tenant(tid):
            await wrap(db).insurers.insert_one({"insurer_code": f"X{len(names)}", "name": f"Fictional Carrier X{len(names)}"})
        names = (await rn.insurers(db, tid))[:3]
    answers = {"vehicles": {"confirmed": False, "remove": ["DEF-456"]}, "address": {"confirmed": True},
               "turnover": {"value": "1,250,000"}, "claims": {"any": False}, "changes": {"text": ""}}
    r, new = await _full_renewal(db, http, tid, KAI_MOTOR, ids, ck, answers, names)
    assert [v["rego"] for v in new["schedule"]["vehicles"]] == ["ABC-123"] and new["schedule"]["turnover"] == 1250000.0
    packet = rn.packet(r, await rn._policy(db, tid, KAI_MOTOR), await rn.payable(db, tid, r))
    assert "no write-back" in packet["text"] and "remove schedule.vehicles[]: DEF-456" in packet["text"]
    from app.jobs.chain_verify import verify_tenant
    slug = (await db.tenants.find_one({"tenant_id": tid}))["slug"]  # tenant-safe: system-only
    assert await verify_tenant(db, slug)


@pytest.mark.asyncio
async def test_full_renewal_on_imported_policy_and_invalid_facts_go_to_review(app_client, mongo_db):
    _, http = app_client
    db = mongo_db
    tid = await _tenant(db, f"import-test-{uuid.uuid4().hex[:4]}")
    safe = wrap(db)
    async with use_tenant(tid):
        await safe.clients.insert_one({"client_id": "imp-c1", "display_name": "Granite Joinery Pty Ltd",
                                       "external": {"iadviser_client_code": "C00002"}})
        for c in "ABC":
            await safe.insurers.insert_one({"insurer_code": f"FC{c}", "name": f"Fictional Carrier {c}"})
        exp = datetime(2027, 4, 9, tzinfo=timezone.utc)
        for pid in ("imp-p1", "imp-p2"):
            await safe.policies.insert_one({"policy_id": pid, "client_id": "imp-c1", "status": "managed", "product": "Motor Vehicle - Commercial",
                                            "insurer_display": "Fictional Carrier A", "inception": exp.replace(year=2026), "expiry": exp,
                                            "premium_amount": 3618.31, "external": {"iadviser_policy_number": f"P-{pid}"},
                                            "imported_from": {"source": "iadviser"}, "schedule": {
                                                "vehicles": [], "addresses": ["146 Granite Street"], "limits": {"mv": 250000.0},
                                                "cover_types": [{"type": "Motor Vehicle - Commercial", "limit_any_one": 250000.0}],
                                                "excess": {"any_one_occurrence": 1000.0, "currency": "AUD"}}})
    ids, ck = await _people(db, tid, "imp-c1")
    answers = {"address": {"confirmed": False, "new_address": "9 Quarry Road, Fictional Bay NSW 2000"}, "turnover": {"value": "800000"},
               "claims": {"any": False}, "changes": {"text": ""}}
    r, new = await _full_renewal(db, http, tid, "imp-p1", ids, ck, answers, ["Fictional Carrier A", "Fictional Carrier B", "Fictional Carrier C"])
    assert new["schedule"]["addresses"] == ["9 Quarry Road, Fictional Bay NSW 2000"] and new["prior_external"]["iadviser_policy_number"] == "P-imp-p1"

    # invalid facts never get invented: a made-up vehicle removal + an unusable turnover → broker review, no change applied
    await rn.tick(db, tid, exp - timedelta(days=60), ["imp-p2"])
    async with use_tenant(tid):
        r2 = await safe.renewals.find_one({"policy_id": "imp-p2"}, {"_id": 0})
    r2 = await rn.request_info(db, tid, r2, by=ids["ae"], role="ae")
    bad = {"vehicles": {"confirmed": False, "remove": ["ZZZ-999"]}, "turnover": {"value": "about a million"}, "claims": {"any": True, "details": "Ute"}}
    assert (await http.post(f"/api/c/renewals/{r2['renewal_id']}/info", headers=ck["owner"], json={"answers": bad})).status_code == 200
    r2 = await rn.get(db, tid, r2["renewal_id"])
    assert r2["needs_review"] and len(r2["issues"]) == 3 and r2["accepted_changes"] == [] and "turnover" not in r2["facts"]
    with pytest.raises(rn.Bad):
        await rn.request_quotes(db, tid, r2, ["Fictional Carrier A"], by=ids["ae"], role="ae")
    await rn.accept_info(db, tid, r2, "Checked with client by phone", by=ids["ae"], role="ae")
    with pytest.raises(rn.Bad):  # bounded mocked channel: 1–3 insurers
        await rn.request_quotes(db, tid, r2, ["Fictional Carrier A", "Fictional Carrier B", "Fictional Carrier C", "Nope"], by=ids["ae"], role="ae")

    # tenant isolation: another tenant's client can't see or act on this renewal
    other = await _tenant(db, f"other-{uuid.uuid4().hex[:4]}")
    async with use_tenant(other):
        await wrap(db).clients.insert_one({"client_id": "imp-c1", "display_name": "Someone Else"})
    _, ck2 = await _people(db, other, "imp-c1")
    assert (await http.get(f"/api/c/renewals/{r['renewal_id']}", headers=ck2["owner"])).status_code == 404
    assert (await http.get("/api/c/renewals", headers=ck2["owner"])).json()["renewals"] == []
    with pytest.raises(LookupError):
        await rn.get(db, other, r["renewal_id"])


@pytest.mark.asyncio
async def test_personal_advice_is_blocked_until_principal_approves(app_client, mongo_db):
    _, http = app_client
    db = mongo_db
    tid = await _tenant(db, f"advice-{uuid.uuid4().hex[:4]}")
    exp = datetime(2027, 6, 30, tzinfo=timezone.utc)
    async with use_tenant(tid):
        await wrap(db).clients.insert_one({"client_id": "c1", "display_name": "Bayside Bakery"})
        await wrap(db).policies.insert_one({"policy_id": "p1", "client_id": "c1", "status": "managed", "product": "Business Pack",
                                            "insurer_display": "Fictional Carrier A", "inception": exp.replace(year=2026), "expiry": exp,
                                            "premium_amount": 2000, "schedule": {"excess": {"any_one_occurrence": 500}, "limits": {"pl": 1}}})
    ids, ck = await _people(db, tid, "c1")
    r = await rn.start(db, tid, await rn._policy(db, tid, "p1"), now=exp - timedelta(days=60))
    kw = {"by": ids["ae"], "role": "ae"}
    r = await rn.accept_info(db, tid, r, "Confirmed by phone", **kw)
    r = await rn.manual_quote(db, tid, r, {"insurer": "Fictional Carrier A", "premium": "2100", "excess": "500"}, by_name="AE", **kw)
    r = await rn.recommend(db, tid, r, r["quotes"][0]["quote_id"], "Given your business is growing, we recommend you stay with Carrier A.",
                           ["Suits your expansion plans"], **kw)
    assert r["recommendation"]["tier"] == "P" and r["recommendation"]["rule_id"] == "RENEWAL-P-01"
    with pytest.raises(rn.Bad, match="Principal"):
        await rn.send_to_client(db, tid, r, **kw)
    assert (await http.get(f"/api/c/renewals/{r['renewal_id']}", headers=ck["owner"])).json().get("recommendation") is None
    with pytest.raises(rn.Bad):  # the author can't approve their own advice
        await rn.approve_advice(db, tid, r, by=ids["ae"])
    r = await rn.approve_advice(db, tid, r, by=ids["principal"])
    r = await rn.send_to_client(db, tid, r, **kw)
    assert r["stage"] == "client_decision" and await _count(db, tid, "RenewalAdviceApproved") == 1
    view = (await http.get(f"/api/c/renewals/{r['renewal_id']}", headers=ck["owner"])).json()
    assert view["recommendation"]["text"].startswith("Given your business")
    # an option that changes the excess is personal advice too (A4-4)
    r2q = {"excess": "1000", "limits": {"pl": 1}}
    assert rn.classify_recommendation("Cheapest.", r2q, await rn._policy(db, tid, "p1"))["tier"] == "P"


@pytest.mark.asyncio
async def test_lapse_reaches_expired_and_reminders_follow_business_days(app_client, mongo_db):
    from app.kernel.certificate import regenerate_certificate_dispatch
    _, http = app_client
    db = mongo_db
    tid = await _tenant(db, f"lapse-{uuid.uuid4().hex[:4]}")
    exp = datetime(2027, 2, 15, 13, tzinfo=timezone.utc)  # Mon 16 Feb 2027, midnight Sydney
    async with use_tenant(tid):
        await wrap(db).clients.insert_one({"client_id": "c1", "display_name": "Harbour Cafe"})
        await wrap(db).policies.insert_one({"policy_id": "p1", "client_id": "c1", "status": "managed", "product": "Business Pack",
                                            "insurer_display": "Fictional Carrier A", "inception": exp.replace(year=2026), "expiry": exp,
                                            "premium_amount": 900, "schedule": {"excess": {"any_one_occurrence": 250}}})
    ids, ck = await _people(db, tid, "c1")
    cert = await regenerate_certificate_dispatch(store=db, tenant_id=tid, policy_id="p1")
    assert (await http.get(f"/api/verifier/{cert['raw_token']}")).json()["verdict"] == "VALID"
    t60 = datetime(2026, 12, 17, 23, tzinfo=timezone.utc)  # Fri 18 Dec 2026 10am Sydney
    await rn.tick(db, tid, t60, ["p1"])
    async with use_tenant(tid):
        r = await wrap(db).renewals.find_one({"policy_id": "p1"}, {"_id": 0})
    await rn.request_info(db, tid, r, by=ids["ae"], role="ae", now=t60)
    # T-45 = Sat 2 Jan 2027 → on or before a business day = Thu 31 Dec 2026 (1 Jan is a holiday)
    assert (await rn.tick(db, tid, datetime(2026, 12, 30, 23, tzinfo=timezone.utc), ["p1"]))["reminders"] == 1
    assert (await rn.tick(db, tid, datetime(2026, 12, 31, 23, tzinfo=timezone.utc), ["p1"]))["reminders"] == 0
    assert (await rn.tick(db, tid, datetime(2027, 1, 17, 11, tzinfo=timezone.utc), ["p1"]))["reminders"] == 0  # 10pm: quiet hours
    assert (await rn.tick(db, tid, datetime(2027, 1, 17, 23, tzinfo=timezone.utc), ["p1"]))["reminders"] == 1  # T-30 (Sun 17 → Fri 15)
    risk = await rn.tick(db, tid, exp - timedelta(days=7), ["p1"])
    assert risk["lapse_risk"] == 1 and (await rn.get(db, tid, r["renewal_id"]))["stage"] == "lapse_risk"
    async with use_tenant(tid):
        kinds = {(n["user_id"], n["kind"]) async for n in wrap(db).notifications.find({"kind": "renewal_lapse_risk"})}
        stuck = await wrap(db).notifications.count_documents({"kind": "renewal_stuck"})
    assert {u for u, _ in kinds} >= {ids["principal"], ids["owner"]} and stuck >= 1
    assert (await rn.tick(db, tid, exp + timedelta(hours=1), ["p1"]))["lapsed"] == 1
    assert (await rn.get(db, tid, r["renewal_id"]))["stage"] == "lapsed"
    assert (await rn._policy(db, tid, "p1"))["status"] == "lapsed"
    v = await http.get(f"/api/verifier/{cert['raw_token']}")
    assert v.status_code == 410 and v.json()["detail"]["verdict"] == "EXPIRED"
    assert await _count(db, tid, "PolicyLapsed") == 1
