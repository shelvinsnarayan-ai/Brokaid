"""R1.5 — payment settings, payables and CRNs.

CRN check digit: BPAY MOD10V01, which is the Luhn algorithm (weights 2,1 from the right,
digits of products summed, check digit = (10 - sum % 10) % 10).
Payables move Owed → Advised (client says paid) → Received (bank confirmed). Received only
ever comes from app.kernel.bankfeed (a bank statement line), never from payment.advised.
"""
from __future__ import annotations

import re
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.db.tenant_safe import wrap
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch

RULES = {"crn": "A unique reference for each amount owed (recommended)",
         "client_code_crn": "Client code digits + item number + check digit"}
DUE_IN_DAYS = 7
Q = Decimal("0.01")


def dec(v) -> Decimal:
    return Decimal(str(v or "0")).quantize(Q)


def luhn_digit(base: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(base)):
        d = int(ch) * (2 if i % 2 == 0 else 1)
        total += d - 9 if d > 9 else d
    return str((10 - total % 10) % 10)


def crn_valid(s: str) -> bool:
    return s.isdigit() and 6 <= len(s) <= 20 and luhn_digit(s[:-1]) == s[-1]


class Invalid(ValueError):
    pass


def validate_settings(b: dict) -> dict:
    bsb = re.sub(r"\D", "", b.get("bsb") or "")
    acct = re.sub(r"\D", "", b.get("account_number") or "")
    biller = re.sub(r"\s", "", b.get("biller_code") or "")
    name = (b.get("account_name") or "").strip()
    if not 3 <= len(name) <= 80:
        raise Invalid("Enter the trust account name (3–80 characters).")
    if len(bsb) != 6:
        raise Invalid("BSB must be 6 digits, like 062-000.")
    if not 5 <= len(acct) <= 9:
        raise Invalid("Account number must be 5–9 digits.")
    if biller and not re.fullmatch(r"\d{4,10}", biller):
        raise Invalid("BPAY biller code must be 4–10 digits.")
    rule = b.get("reference_rule") or "crn"
    if rule not in RULES:
        raise Invalid("Choose a reference rule.")
    return {"account_name": name, "bsb": f"{bsb[:3]}-{bsb[3:]}", "account_number": acct, "biller_code": biller or None,
            "reference_rule": rule}


async def get_settings(store, tid: str) -> dict | None:
    async with use_tenant(tid):
        return await wrap(store).payment_settings.find_one({}, {"_id": 0})


async def save_settings(store, tid: str, body: dict, user_id: str, role: str) -> dict:
    s = validate_settings(body)
    safe = wrap(store)
    async with use_tenant(tid):
        prev = await safe.payment_settings.find_one({}) or {}
        changed = sorted(k for k in s if prev.get(k) != s[k])
        await safe.payment_settings.update_one({}, {"$set": {**s, "updated_at": datetime.now(timezone.utc), "updated_by": user_id}},
                                               upsert=True)
        if changed:
            await append_via_dispatch(store, tid, "PaymentSettingsUpdated", actor={"kind": "human", "id": user_id, "name": role},
                                      payload={"changed": changed, "bsb": s["bsb"], "account_last3": s["account_number"][-3:],
                                               "biller_code": s["biller_code"], "reference_rule": s["reference_rule"]},
                                      idempotency_key=f"paysettings:{uuid.uuid4()}")
    return s


def state_of(p: dict) -> str:
    got, owed = dec(p.get("received")), dec(p.get("amount"))
    if got > 0:
        return "received" if got == owed else "overpaid" if got > owed else "partial"
    return "advised" if p.get("advised_at") else "owed"


async def _new_crn(safe, rule: str, client: dict | None) -> str:
    for _ in range(20):
        if rule == "client_code_crn" and client and (code := re.sub(r"\D", "", (client.get("external") or {}).get("iadviser_client_code", ""))):
            base = f"{code[-5:].rjust(5, '0')}{secrets.randbelow(10 ** 4):04d}"
        else:
            base = f"{secrets.randbelow(9 * 10 ** 8) + 10 ** 8}"
        crn = base + luhn_digit(base)
        if not await safe.payables.find_one({"crn": crn}):
            return crn
    raise RuntimeError("could not allocate a CRN")


async def ensure_payable(store, tid: str, *, envelope_id: str, policy_id: str, amount, currency: str = "AUD",
                         kind: str = "endorsement_premium") -> dict:
    safe = wrap(store)
    async with use_tenant(tid):
        p = await safe.payables.find_one({"envelope_id": envelope_id}, {"_id": 0})
        if p:
            return p
        pol = await safe.policies.find_one({"policy_id": policy_id}) or {}
        client = await safe.clients.find_one({"client_id": pol.get("client_id")})
        rule = ((await safe.payment_settings.find_one({})) or {}).get("reference_rule") or "crn"
        p = {"payable_id": str(uuid.uuid4()), "crn": await _new_crn(safe, rule, client), "kind": kind, "envelope_id": envelope_id,
             "policy_id": policy_id, "client_id": pol.get("client_id"), "amount": str(dec(amount)), "currency": currency,
             "received": "0.00", "advised_at": None, "created_at": datetime.now(timezone.utc),
             "due_date": (date.today() + timedelta(days=DUE_IN_DAYS)).isoformat(),
             "test": bool(pol.get("test") or (client or {}).get("test"))}
        p["state"] = state_of(p)
        await safe.payables.insert_one(dict(p))
        p.pop("_id", None)
        return p


async def mark_advised(store, tid: str, envelope_id: str) -> None:
    """payment.advised → Advised. Never Received."""
    safe = wrap(store)
    async with use_tenant(tid):
        p = await safe.payables.find_one({"envelope_id": envelope_id})
        if p and not p.get("advised_at"):
            p["advised_at"] = datetime.now(timezone.utc)
            await safe.payables.update_one({"payable_id": p["payable_id"]}, {"$set": {"advised_at": p["advised_at"], "state": state_of(p)}})


MANUAL_ONLY_MSG = "Your broker will send payment details"


def details(payable: dict, settings: dict | None, mode: str = "mocked_allowed") -> dict:
    out = {"amount": payable["amount"], "currency": payable["currency"], "due_date": payable["due_date"], "crn": payable["crn"],
           "state": payable["state"]}
    if settings:
        return {**out, "mocked": False,
                "bpay": {"biller_code": settings.get("biller_code"), "reference": payable["crn"], "mocked": False}
                if settings.get("biller_code") else None,
                "eft": {"account_name": settings["account_name"], "bsb": settings["bsb"], "account_number": settings["account_number"],
                        "reference": payable["crn"], "mocked": False}}
    if mode == "manual_only":  # R1.6: a real tenant never shows fake payment details
        return {**out, "mocked": False, "pending_details": True, "message": MANUAL_ONLY_MSG, "bpay": None, "eft": None}
    from app.kernel.mocks import payment_rail as pr
    return {**out, "mocked": True, "bpay": {"biller_code": pr.BPAY_BILLER_CODE_MOCKED, "reference": payable["crn"], "mocked": True},
            "eft": {"account_name": pr.EFT_ACCOUNT_NAME_MOCKED, "bsb": pr.EFT_BSB_MOCKED, "account_number": pr.EFT_ACCOUNT_MOCKED,
                    "reference": payable["crn"], "mocked": True}}
