"""R3.1 bank-style client app: big figure per type + fallback, Home grouping, /api/c/activity isolation, policy page."""
from __future__ import annotations

import pathlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db.tenant_safe import wrap
from app.kernel import client_home as ch
from app.kernel.tenant_ctx import use_tenant
from tests.test_r16_renewals import _people, _tenant

ALLOWED = {"type", "at", "date", "title", "subtitle", "amount", "state", "crn", "policy_id", "href"}


def test_big_figure_per_type_and_fallback():
    motor = {"product": "Motor — commercial vehicle", "schedule": {}}
    assert ch.big_figure(motor, {"agreed_value": 42000}) == {"text": "A$42,000", "label": "agreed value", "kind": "value"}
    assert ch.big_figure(motor, {"market_value": 52000})["label"] == "market value"
    pl = {"product": "Public Liability", "schedule": {"cover_types": [{"type": "Public liability", "limit_any_one": 20000000}]}}
    assert ch.big_figure(pl) == {"text": "A$20M", "label": "limit", "kind": "value"}
    tools = {"product": "Tools and equipment", "schedule": {"sum_insured": 45000}}
    assert ch.big_figure(tools)["label"] == "sum insured" and ch.big_figure(tools)["text"] == "A$45,000"
    assert ch.big_figure({"product": "Cyber", "schedule": {"excess": 1000}}) == {"text": "A$1,000", "label": "excess", "kind": "excess"}
    assert ch.big_figure({"product": "Cyber", "schedule": {}}) is None  # never invented
    assert ch.big_figure(motor, {"rego": "X"}) is None


def test_groups():
    assert ch.group_of({"product": "Motor — fleet"}) == "vehicles"
    assert ch.group_of({"product": "Business Pack — contents"}) == "business"
    assert ch.group_of({"product": "Home and contents"}) == "home"


@pytest.mark.asyncio
async def test_home_activity_policy_isolation(app_client, mongo_db):
    _, http = app_client
    db = mongo_db
    tid = await _tenant(db, f"r31-{uuid.uuid4().hex[:4]}")
    now = datetime.now(timezone.utc)
    async with use_tenant(tid):
        s = wrap(db)
        for cid, name in (("c1", "Harbour Cafe"), ("c2", "Other Client")):
            await s.clients.insert_one({"client_id": cid, "display_name": name})
        base = {"insurer_display": "Fictional Carrier A", "inception": now - timedelta(days=30), "expiry": now + timedelta(days=300)}
        await s.policies.insert_one({"policy_id": "mot", "client_id": "c1", "status": "managed", "product": "Motor — commercial vehicle",
                                     "schedule": {"vehicles": [{"rego": "AAA111", "make": "Toyota", "model": "Hilux", "agreed_value": 42000},
                                                               {"rego": "BBB222", "make": "Isuzu", "model": "D-Max"}], "excess": 800}, **base})
        await s.policies.insert_one({"policy_id": "pl", "client_id": "c1", "status": "managed", "product": "Public Liability",
                                     "schedule": {"cover_types": [{"type": "Public liability", "limit_any_one": 20000000}]}, **base})
        await s.policies.insert_one({"policy_id": "tst", "client_id": "c1", "status": "managed", "product": "Test Pack", "test": True,
                                     "schedule": {}, **base})
        await s.policies.insert_one({"policy_id": "foreign", "client_id": "c2", "status": "managed", "product": "Foreign Cyber",
                                     "schedule": {}, **base})
        for pid, cid, test in (("pl", "c1", False), ("foreign", "c2", False), ("pl", "c1", True)):
            await s.payables.insert_one({"payable_id": uuid.uuid4().hex, "client_id": cid, "policy_id": pid, "amount": 540.0, "currency": "AUD", "crn": uuid.uuid4().hex[:8],
                                         "state": "owed", "created_at": now, "due_date": now + timedelta(days=10), "test": test})
        await s.claims.insert_one({"claim_id": "cl2", "client_id": "c2", "policy_id": "foreign", "claim_reference": "CLM-X",
                                   "lodged_at": now, "stage": "lodged"})
    _, cookies = await _people(db, tid, "c1")
    h = cookies["owner"]
    home = (await http.get("/api/c/home", headers=h)).json()
    rows = [r for g in home["cover"] for r in g["rows"]]
    assert {r["policy_id"] for r in rows} == {"mot", "pl"}
    motor = [r for r in rows if r["policy_id"] == "mot"]
    assert len(motor) == 2 and {r["anchor"] for r in motor} == {"AAA111", "BBB222"}  # one row per vehicle
    assert next(r for r in motor if r["anchor"] == "BBB222")["figure"]["label"] == "excess"  # fallback, not invented
    assert home["client_name"] == "Harbour Cafe" and [a["kind"] for a in home["attention"]] == ["payment_due"]

    act = (await http.get("/api/c/activity", headers=h)).json()
    assert "due" in act, act
    assert len(act["due"]) == 1 and act["due"][0]["amount"] == "A$540"
    assert act["panel"]["total_due"] == "A$540" and act["panel"]["open_claims"]["count"] == 0
    dues_claims = (await http.get("/api/c/activity?f=claims", headers=h)).json()
    assert len(dues_claims["due"]) == 1 and dues_claims["panel"]["total_due"] == "A$540"  # panel ignores the feed filter
    blob = str(act)
    assert "Foreign" not in blob and "CLM-X" not in blob and "Test Pack" not in blob
    for i in act["items"]:
        assert set(i) <= ALLOWED and "." not in i["title"].split(" ")[0] and "_" not in i["title"]
    assert (await http.get("/api/c/activity?f=claims", headers=h)).json()["items"] == []
    assert (await http.get("/api/c/activity?f=bogus", headers=h)).status_code == 422

    pg = (await http.get("/api/c/policy/mot", headers=h)).json()
    assert pg["figure"] is None or pg["figure"]["kind"] in ("value", "excess")
    assert [v["rego"] for v in pg["vehicles"]] == ["AAA111", "BBB222"]
    for pid in ("foreign", "tst", "nope"):
        assert (await http.get(f"/api/c/policy/{pid}", headers=h)).status_code == 404


def test_old_client_routes_redirect():
    app = (pathlib.Path(__file__).resolve().parents[2] / "frontend/src/App.tsx").read_text()
    for old, new in (("cover", "/c"), ("money", "/c/activity?f=payments"), ("claims", "/c/activity?f=claims"), ("ask", "/c/messages/broker")):
        assert f'<Route path="{old}" element={{<Navigate to="{new}" replace />}} />' in app
    for new in ("activity", "messages", "messages/broker", "policy/:policy_id", "profile"):
        assert f'path="{new}"' in app
