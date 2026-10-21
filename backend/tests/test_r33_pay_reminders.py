"""R3.3 Home payment reminders: 3-day window + overdue, combined card, Pay-now routing per kind, isolation, test data, authority."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.db.tenant_safe import wrap
from app.kernel import client_home as ch
from app.kernel.tenant_ctx import use_tenant
from tests.test_r16_renewals import _people, _tenant

TODAY = date(2026, 10, 21)


def _pay(due_days, state="owed", kind="invoice", pid="pol", **kw):
    p = {"payable_id": uuid.uuid4().hex, "policy_id": pid, "amount": "465.00", "received": "0.00", "currency": "AUD", "crn": "1",
         "kind": kind, "due_date": (TODAY + timedelta(days=due_days)).isoformat(), "advised_at": None, "state": state, **kw}
    if state == "advised":
        p["advised_at"] = datetime.now(timezone.utc)
    if state == "received":
        p["received"] = "465.00"
    return p


def _ctx(*pays, renewals=()):
    return {"policies": [{"policy_id": "pol", "product": "Business pack — contents"}], "payables": list(pays),
            "renewals": list(renewals), "claims": [], "docs": [], "acceptances": []}


def test_window_boundaries_and_wording():
    assert ch.reminders(_ctx(_pay(4)), TODAY, True) is None  # 4 days away: not yet
    assert ch.reminders(_ctx(_pay(3)), TODAY, True)["item"]["due_text"] == "due in 3 days"
    assert ch.reminders(_ctx(_pay(1)), TODAY, True)["item"]["due_text"] == "due tomorrow"
    r = ch.reminders(_ctx(_pay(0)), TODAY, True)["item"]
    assert r["due_text"] == "due today" and not r["overdue"]
    late = ch.reminders(_ctx(_pay(-2)), TODAY, True)["item"]
    assert late["overdue"] and late["due_text"] == "was due on 19 Oct"
    assert ch.reminders(_ctx(_pay(1, state="advised"), _pay(1, state="received")), TODAY, True) is None


def test_pay_now_routes_by_kind_and_combined_card():
    e = _pay(1, kind="endorsement_premium", envelope_id="env1")
    assert ch.reminders(_ctx(e), TODAY, True)["item"]["href"] == "/c/cover/pol/pay/env1"
    r = _pay(1, kind="renewal_premium", envelope_id="env2")
    assert ch.reminders(_ctx(r, renewals=[{"renewal_id": "ren9", "envelope_id": "env2"}]), TODAY, True)["item"]["href"] == "/c/renewal/ren9"
    i = _pay(2)
    assert ch.reminders(_ctx(i), TODAY, True)["item"]["href"] == f"/c/activity?f=payments#due-{i['payable_id']}"
    both = ch.reminders(_ctx(_pay(2), _pay(-1), _pay(9)), TODAY, True)
    assert both == {"combined": True, "count": 2, "total": "A$930", "overdue": True, "href": "/c/activity?f=payments", "can_pay": True}
    assert ch.reminders(_ctx(i), TODAY, False)["item"]["href"] is None  # no payment authority → info only


@pytest.mark.asyncio
async def test_home_reminder_isolation_test_data_and_authority(app_client, mongo_db):
    _, http = app_client
    db = mongo_db
    tid = await _tenant(db, f"r33-{uuid.uuid4().hex[:4]}")
    soon = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
    async with use_tenant(tid):
        s = wrap(db)
        for cid in ("c1", "c2"):
            await s.clients.insert_one({"client_id": cid, "display_name": cid})
            await s.policies.insert_one({"policy_id": f"p-{cid}", "client_id": cid, "status": "managed", "product": f"Pack {cid}",
                                         "schedule": {}, "expiry": datetime.now(timezone.utc) + timedelta(days=200)})
        for cid, test in (("c1", False), ("c2", False), ("c1", True)):
            await s.payables.insert_one({"payable_id": uuid.uuid4().hex, "client_id": cid, "policy_id": f"p-{cid}", "amount": "120.00",
                                         "received": "0.00", "currency": "AUD", "crn": uuid.uuid4().hex[:9], "kind": "invoice",
                                         "state": "owed", "due_date": soon, "created_at": datetime.now(timezone.utc), "test": test})
    _, cookies = await _people(db, tid, "c1")
    rem = (await http.get("/api/c/home", headers=cookies["owner"])).json()["reminder"]
    home = (await http.get("/api/c/home", headers=cookies["owner"])).json()
    assert "payment_due" not in [a["kind"] for a in home["attention"]]  # the reminder replaces the attention card
    assert rem["combined"] is False and rem["item"]["amount"] == "A$120" and rem["item"]["policy"] == "Pack c1"  # not c2, not test
    assert rem["item"]["href"].startswith("/c/activity?f=payments#due-")
    bk = (await http.get("/api/c/home", headers=cookies["bookkeeper"])).json()["reminder"]
    assert bk["item"]["href"] and bk["can_pay"] is True  # bookkeeper has can_pay by default
    async with use_tenant(tid):
        await wrap(db).memberships.update_one({"client_id": "c1", "role": "bookkeeper"}, {"$set": {"authority.can_pay": False}})
    bk = (await http.get("/api/c/home", headers=cookies["bookkeeper"])).json()["reminder"]
    assert bk["item"]["href"] is None and bk["can_pay"] is False
    act = (await http.get("/api/c/activity", headers=cookies["bookkeeper"])).json()
    assert act["due"] and all(d["pay_href"] is None for d in act["due"])  # Activity follows the same rule
