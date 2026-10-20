"""R2.7: Renewal expiry review — buckets, suspension holds, test-data exclusion, roles, nudge throttle, formula-safe CSV."""
from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db.tenant_safe import wrap
from app.kernel import holidays
from app.kernel import renewal_review as rv
from app.kernel import renewals as rn
from app.kernel.tenant_ctx import use_tenant
from tests.test_r16_renewals import _people, _tenant
from tests.test_r20_acceptance import _staff

NOW = datetime.now(timezone.utc)
TODAY = NOW.astimezone(holidays.SYD).replace(hour=17, minute=0, second=0, microsecond=0)


def _exp(days):
    return (TODAY + timedelta(days=days)).astimezone(timezone.utc)


async def _setup(db):
    tid = await _tenant(db, f"r27-{uuid.uuid4().hex[:4]}")
    async with use_tenant(tid):
        await wrap(db).clients.insert_one({"client_id": "c1", "display_name": "=HYPERLINK(\"x\")"})  # formula-looking name
        await wrap(db).clients.insert_one({"client_id": "ct", "display_name": "Test Co", "test": True})
    ids, _ = await _people(db, tid, "c1")
    op = str(uuid.uuid4())
    async with use_tenant(tid):
        await wrap(db).users.insert_one({"user_id": op, "email": f"op-{op[:6]}@r27.example", "display_name": "Olly R27", "kind": "operator"})
        await wrap(db).user_brokerage_roles.insert_one({"user_id": op, "tenant_id": tid, "brokerage_id": "b", "role": "operator", "active": True})
    ids["operator"] = op
    return tid, ids


async def _policy(db, tid, pid, days, client="c1", **extra):
    async with use_tenant(tid):
        await wrap(db).policies.insert_one({"policy_id": pid, "client_id": client, "status": "managed", "product": f"Class {pid}",
                                            "insurer_display": "Fictional Carrier A", "inception": _exp(days) - timedelta(days=365),
                                            "expiry": _exp(days), "premium_amount": 900, "schedule": {}, **extra})


def test_bucket_boundaries():
    assert [rv.bucket(d) for d in (31, 30, 15, 14, 8, 7, 0, -1)] == [None, "d30", "d30", "d14", "d14", "d7", "d7", "expired"]


@pytest.mark.asyncio
async def test_review_buckets_holds_exclusions_roles_nudge_and_csv(app_client, mongo_db):
    _, http = app_client
    db = mongo_db
    tid, ids = await _setup(db)
    for days in (30, 15, 14, 8, 7, 0, 31):
        await _policy(db, tid, f"p{days}", days)
    await _policy(db, tid, "ptest", 5, test=True)          # test-flagged policy
    await _policy(db, tid, "ptc", 5, client="ct")          # test client
    await _policy(db, tid, "pheld", 20)
    r = await rn.start(db, tid, await rn._policy(db, tid, "pheld"), now=NOW - timedelta(days=30))
    async with use_tenant(tid):  # passed expiry while suspended → held (R2.6)
        await wrap(db).renewals.update_one({"renewal_id": r["renewal_id"]}, {"$set": {"expiry": _exp(-1), "lapse_hold": True}})
    d = await rv.review(db, tid)
    got = {b["key"]: [x["policy_id"] for x in b["rows"]] for b in d["buckets"]}
    assert got == {"expired": ["pheld"], "d7": ["p0", "p7"], "d14": ["p8", "p14"], "d30": ["p15", "p30"]}
    held = d["buckets"][0]["rows"][0]
    assert held["actions"]["confirm_lapse"] and "lapse_hold" in held["flags"] and held["days_text"] == "1 day overdue"
    assert d["buckets"][1]["rows"][0]["stage_label"] == "Not started" and d["buckets"][1]["rows"][0]["days_text"] == "Expires today"
    assert d["badge"] == 3 and held["ae"]["status"] == "ok"

    op, pr, ae = (await _staff(db, tid, ids["operator"], "operator"), await _staff(db, tid, ids["principal"], "principal"),
                  await _staff(db, tid, ids["ae"], "ae"))
    assert (await http.get("/api/o/renewal-review", headers=ae)).status_code == 403
    assert (await http.get("/api/o/renewal-review.csv", headers=ae)).status_code == 403
    pv = await http.get("/api/o/renewal-review", headers=pr)
    assert pv.status_code == 200 and pv.json()["can_act"] is False
    assert (await http.post("/api/o/renewal-review/nudge", headers=pr, json={"policy_id": "p7"})).status_code == 403
    rid = held["renewal_id"]
    assert (await http.post(f"/api/o/renewals/{rid}/confirm-lapse", headers=pr, json={"reason": "client gone"})).status_code == 403
    assert (await http.post(f"/api/o/renewals/{rid}/confirm-lapse", headers=op, json={})).status_code == 422  # reason required
    ov = (await http.get("/api/o/renewal-review", headers=op)).json()
    assert ov["can_act"] is True and ov["buckets"][0]["count"] == 1

    # nudge: ledgered, throttled to one per renewal/policy per day
    n1 = await http.post("/api/o/renewal-review/nudge", headers=op, json={"policy_id": "p7"})
    assert n1.status_code == 200 and n1.json()["sent_to"] == 1
    assert (await http.post("/api/o/renewal-review/nudge", headers=op, json={"policy_id": "p7"})).status_code == 429
    assert (await http.post("/api/o/renewal-review/nudge", headers=op, json={"renewal_id": rid})).status_code == 200
    async with use_tenant(tid):
        assert await wrap(db).events.count_documents({"type": "RenewalNudgeSent"}) == 2
        assert await wrap(db).notifications.count_documents({"kind": "renewal_nudge", "user_id": ids["ae"]}) >= 1

    # CSV is formula-safe
    c = await http.get("/api/o/renewal-review.csv", headers=op)
    assert c.status_code == 200 and c.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(c.text)))
    assert rows[0][0] == "Section" and len(rows) == 1 + 7
    assert all(not cell.startswith(("=", "+", "-", "@")) for row in rows for cell in row)
    assert any(cell.startswith("'=HYPERLINK") for row in rows for cell in row)

    # confirm lapse (Operator, with reason, ledgered) → stays under Expired, no longer held
    ok = await http.post(f"/api/o/renewals/{rid}/confirm-lapse", headers=op, json={"reason": "Client sold the business"})
    assert ok.status_code == 200, ok.text
    async with use_tenant(tid):
        ev = await wrap(db).events.find_one({"type": "RenewalLapseConfirmed", "payload.renewal_id": rid})
    assert ev["payload"]["reason"] == "Client sold the business"
    after = (await rv.review(db, tid))["buckets"][0]["rows"]
    assert after[0]["stage"] == "lapsed" and not after[0]["actions"]["confirm_lapse"]


@pytest.mark.asyncio
async def test_paid_lapsed_shows_trust_and_refund_link_and_disabled_ae(mongo_db):
    db = mongo_db
    tid, ids = await _setup(db)
    await _policy(db, tid, "pp", 20)
    r = await rn.start(db, tid, await rn._policy(db, tid, "pp"), now=NOW - timedelta(days=30))
    async with use_tenant(tid):
        await wrap(db).payables.insert_one({"payable_id": "pay1", "amount": "540.00", "received": "540.00", "advised_at": None})
        await wrap(db).renewals.update_one({"renewal_id": r["renewal_id"]}, {"$set": {
            "stage": "lapsed", "expiry": _exp(-3), "payable_id": "pay1", "refund_review": {"state": "open"}}})
        await wrap(db).users.update_one({"user_id": ids["ae"]}, {"$set": {"disabled": True}})
    row = (await rv.review(db, tid))["buckets"][0]["rows"][0]
    assert row["money"] == {"state": "Received", "held_in_trust": True} and "refund_review" in row["flags"]
    assert row["actions"]["refund_or_bind"] == f"/o/renewals?r={r['renewal_id']}" and row["ae"]["name"] == "AE disabled"
    assert row["days_text"] == "3 days overdue"
