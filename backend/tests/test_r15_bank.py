"""R1.5 — bank feed, CRN matching, trust record and reconciliation (synthetic statements)."""
from __future__ import annotations

import os
import re
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.db.tenant_safe import wrap
from app.kernel import bankfeed as bf
from app.kernel import payments as pm
from app.kernel import trail_mongo
from app.kernel.tenant_ctx import use_tenant
from app.scripts import bank_synth as synth


async def _setup(db, n=3):
    await bf.ensure_indexes(db)
    tid = str(uuid.uuid4())
    async with use_tenant(tid):
        safe = wrap(db)
        await trail_mongo.create_tenant(safe, slug=f"t-bank-{tid[:6]}", display_name="Bank Test", tenant_id_override=tid)
        pays = []
        for i, (name, amt) in enumerate([("Kai Pacific Logistics", "85.00"), ("Bayside Bakery", "412.50"), ("Granite Joinery", "230.00")][:n]):
            await safe.clients.insert_one({"client_id": f"c{i}", "display_name": name})
            await safe.policies.insert_one({"policy_id": f"p{i}", "client_id": f"c{i}", "status": "managed", "product": "Motor"})
    for i, amt in enumerate(["85.00", "412.50", "230.00"][:n]):
        pays.append(await pm.ensure_payable(db, tid, envelope_id=f"e{i}", policy_id=f"p{i}", amount=amt))
    return tid, pays


async def _count(db, tid, typ):
    async with use_tenant(tid):
        return await wrap(db).events.count_documents({"type": typ})


async def _txns(db, tid):
    async with use_tenant(tid):
        return {t["description"]: t async for t in wrap(db).bank_txns.find({})}


def test_crn_check_digit_and_settings_validation():
    assert pm.luhn_digit("7992739871") == "3" and pm.crn_valid("79927398713") and not pm.crn_valid("79927398710")
    with pytest.raises(pm.Invalid):
        pm.validate_settings({"account_name": "Trust", "bsb": "12-345", "account_number": "12345678"})
    with pytest.raises(pm.Invalid):
        pm.validate_settings({"account_name": "Trust", "bsb": "062000", "account_number": "12345678", "biller_code": "12a"})
    assert pm.validate_settings({"account_name": "BurMac Trust", "bsb": "062000", "account_number": "1234 5678",
                                 "biller_code": "123456"})["bsb"] == "062-000"


def test_four_bank_formats_and_ofx_parse_with_continuity():
    ts = synth.lines([("1234567897", "85.00", "Kai"), ("2345678907", "412.50", "Bayside")])
    for k, (fn, ext) in synth.WRITERS.items():
        p = bf.parse(fn(ts).encode(), f"s.{ext}")
        assert p["format"] == k and len(p["txns"]) == len(ts), k
        c = bf.continuity(p["txns"], p["closing_hint"])
        assert c["ok"], k
        if k != "anz":
            assert c["opening"] == Decimal("10000.00") and c["closing"] == ts[-1]["balance"], k
    gap = [t for i, t in enumerate(ts) if i != 2]
    c = bf.continuity(bf.parse(synth.cba(gap).encode(), "g.csv")["txns"])
    assert not c["ok"] and c["gaps"][0]["missing_amount"] == "312.40"
    with pytest.raises(bf.NeedsMapping):
        bf.parse(b"When,How much,What\n01/10/2026,5.00,x\n", "odd.csv")
    m = bf.parse(b"When,How much,What\n01/10/2026,5.00,x\n", "odd.csv", {"date": "When", "amount": "How much", "description": "What"})
    assert m["format"] == "mapped" and m["txns"][0]["amount"] == Decimal("5.00")


@pytest.mark.asyncio
async def test_import_match_dedupe_unmatch_and_reconcile(mongo_db):
    db = mongo_db
    tid, pays = await _setup(db)
    await pm.mark_advised(db, tid, "e1")  # client says paid → Advised, never Received
    async with use_tenant(tid):
        assert (await wrap(db).payables.find_one({"envelope_id": "e1"}))["state"] == "advised"
    assert await _count(db, tid, "PremiumReceived") == 0
    items = [(pays[0]["crn"], "85.00", "Kai Pacific Logistics"), (pays[1]["crn"], "412.50", "Bayside Bakery"),
             (pays[2]["crn"], "230.00", "Granite Joinery")]
    ts = synth.lines(items)
    r = await bf.import_statement(db, tid, data=synth.cba(ts[:4]).encode(), filename="oct-a.csv", user_id="u", storage_path=None)
    assert r["statement"]["new_txns"] == 4 and r["statement"]["continuity"]["ok"]
    tx = await _txns(db, tid)
    crn_lines = [d for d in tx if d.startswith("BPAY")]
    assert all(tx[d]["status"] == "matched" and tx[d]["match"]["kind"] == "auto_crn" and tx[d]["match"]["review_required"] for d in crn_lines)
    fuzzy = next(d for d in tx if d.startswith("TRANSFER FROM"))
    assert tx[fuzzy]["status"] == "suggested" and tx[fuzzy]["suggestions"][0]["payable_id"] == pays[2]["payable_id"]
    assert tx["DIRECT CREDIT UNKNOWN REMITTER 4471"]["status"] == "unmatched"
    assert await _count(db, tid, "PremiumReceived") == 2
    async with use_tenant(tid):
        ev = await wrap(db).events.find_one({"type": "PremiumReceived"})
        assert ev["payload"]["source"] == "bank_statement" and ev["payload"]["txn_fingerprint"] and ev["payload"]["crn"]

    # same file again → file-level dedupe; overlapping statement in ANOTHER bank format → only new lines
    assert (await bf.import_statement(db, tid, data=synth.cba(ts[:4]).encode(), filename="again.csv", user_id="u",
                                      storage_path=None))["duplicate_file"]
    r2 = await bf.import_statement(db, tid, data=synth.nab(ts[2:]).encode(), filename="oct-b.csv", user_id="u", storage_path=None)
    assert r2["statement"]["new_txns"] == len(ts) - 4 and r2["statement"]["duplicate_txns"] == 2
    assert await _count(db, tid, "PremiumReceived") == 2

    # fuzzy needs a human; unmatch with a reason; re-match works
    assert await bf.confirm_match(db, tid, tx[fuzzy]["txn_id"], pays[2]["payable_id"], "u-ae", "ae") == "matched"
    assert await _count(db, tid, "PremiumReceived") == 3
    with pytest.raises(ValueError):
        await bf.unmatch(db, tid, tx[fuzzy]["txn_id"], " ", "u-ae", "ae")
    await bf.unmatch(db, tid, tx[fuzzy]["txn_id"], "Wrong client", "u-ae", "ae")
    async with use_tenant(tid):
        assert (await wrap(db).payables.find_one({"envelope_id": "e2"}))["state"] == "owed"
    assert await _count(db, tid, "PremiumReceiptReversed") == 1
    async with use_tenant(tid):  # suggestions come back after an unmatch; it is not auto-rematched
        back = await wrap(db).bank_txns.find_one({"txn_id": tx[fuzzy]["txn_id"]})
    assert back["status"] == "suggested" and back["suggestions"][0]["payable_id"] == pays[2]["payable_id"]
    await bf.confirm_match(db, tid, tx[fuzzy]["txn_id"], pays[2]["payable_id"], "u-ae", "ae")
    assert await _count(db, tid, "PremiumReceived") == 4

    # trust: insurer payment from the outgoing line; the bank fee is left unallocated (deliberate discrepancy)
    tx = await _txns(db, tid)
    await bf.record_payment(db, tid, by="u", role="ae", kind="insurer_payment", txn_id=tx["TRANSFER TO FICTIONAL CARRIER A PREMIUMS OCT"]["txn_id"],
                            insurer="Fictional Carrier A")
    rec = await bf.reconciliation(db, tid, date(2026, 12, 31))
    keys = {i["key"]: i["amount"] for i in rec["items"]}
    assert rec["balanced"] and keys["unallocated_out"] == "-15.00" and keys["unmatched_in"] == "312.40" and keys["opening"] == "10000.00"
    assert Decimal(rec["trust_balance"]) == Decimal("85.00") + Decimal("412.50") + Decimal("230.00") - Decimal("1500.00")
    # a statement with a missing line shows as a gap, and is explained
    later = synth.lines(items, start=date(2026, 11, 1), opening=ts[-1]["balance"])
    await bf.import_statement(db, tid, data=synth.westpac([t for i, t in enumerate(later) if i != 3]).encode(), filename="nov.csv",
                              user_id="u", storage_path=None)
    rec = await bf.reconciliation(db, tid, date(2026, 12, 31))
    assert rec["gaps"] and rec["balanced"] and any(i["key"] == "gaps" for i in rec["items"])
    await bf.sign_off(db, tid, date(2026, 12, 31), "Month end", "u-ada")
    assert await _count(db, tid, "TrustReconciliationSignedOff") == 1


@pytest.mark.asyncio
async def test_tenant_isolation_crn_from_other_tenant_never_matches(mongo_db):
    db = mongo_db
    a, pays = await _setup(db, 1)
    b, _ = await _setup(db, 1)
    ts = [{"date": date(2026, 10, 2), "amount": Decimal("85.00"), "description": f"BPAY {pays[0]['crn']} KAI", "balance": Decimal("85.00")}]
    await bf.import_statement(db, b, data=synth.cba(ts).encode(), filename="b.csv", user_id="u", storage_path=None)
    assert await _count(db, b, "PremiumReceived") == 0
    assert (await bf.reconciliation(db, a, date(2026, 12, 31)))["bank_balance"] == "0.00"


def test_premium_received_is_only_emitted_by_the_bank_feed():
    app = Path(__file__).resolve().parents[1] / "app"
    emitters = [str(p.relative_to(app)) for p in app.rglob("*.py")
                if re.search(r'append_\w*\([^)]*"PremiumReceived"|_event\(store, tid, "PremiumReceived"', p.read_text(), re.S)]
    assert emitters == ["kernel/bankfeed.py"], emitters


BASE = os.environ.get("REACT_APP_BACKEND_URL", "")


def _as(email):
    c = httpx.Client(base_url=BASE, timeout=60)
    assert c.post("/api/auth/dev/impersonate", json={"email": email}).status_code == 200
    return c


def test_bank_api_roles_and_settings():
    op, ada, cass, kai = (_as(e) for e in ("operator@harbourline.example", "principal@harbourline.example",
                                           "ae@harbourline.example", "owner@fictionalclient.example"))
    bad = op.put("/api/o/payment-settings", json={"account_name": "Harbourline Trust", "bsb": "12", "account_number": "12345678"})
    assert bad.status_code == 422 and "BSB" in bad.text
    assert ada.put("/api/o/payment-settings", json={"account_name": "x", "bsb": "062000", "account_number": "123456"}).status_code == 403
    ok = op.put("/api/o/payment-settings", json={"account_name": "Harbourline Trust", "bsb": "062000", "account_number": "10001234",
                                                  "biller_code": "123456"})
    assert ok.status_code == 200 and ok.json()["settings"]["bsb"] == "062-000"
    assert cass.get("/api/o/bank/reconciliation").status_code == 403
    assert op.post("/api/o/bank/reconciliation/sign-off", json={"as_at": "2026-12-31"}).status_code == 403
    up = ada.post("/api/o/bank/statements", files={"file": ("anz.csv", synth.anz(synth.lines([])).encode(), "text/csv")})
    assert up.status_code == 200 and up.json()["statement"]["format"] == "anz"
    assert ada.post("/api/o/bank/reconciliation/sign-off", json={"as_at": "2026-12-31", "note": "test"}).status_code == 200
    assert kai.get("/api/c/money").status_code == 200 and kai.get("/api/o/bank/txns").status_code in (401, 403)
    assert ada.get("/api/o/bank/reconciliation/export.pdf").content[:4] == b"%PDF"


async def test_premium_received_alert_reaches_the_client(mongo_db):
    from app.kernel import notify
    tid, cid, uid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    async with use_tenant(tid):
        safe = wrap(mongo_db)
        await safe.clients.insert_one({"client_id": cid, "display_name": "Bank Alert Client"})
        await safe.user_client_links.insert_one({"user_id": uid, "client_id": cid})
        await safe.memberships.insert_one({"user_id": uid, "client_id": cid, "role": "owner", "revoked_at": None})
    await notify.on_event(mongo_db, tid, "PremiumReceived", {"client_id": cid, "payable_id": "pay-1", "amount": "10.00"}, "ev-1")
    async with use_tenant(tid):
        n = await wrap(mongo_db).notifications.find_one({"user_id": uid, "kind": "payment_received"})
    assert n and n["link"] == "/c/money"
