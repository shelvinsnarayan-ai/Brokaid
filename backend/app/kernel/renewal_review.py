"""R2.7 — Renewal expiry review: one Operator screen, four non-overlapping buckets by calendar days to expiry (Sydney).

Expired (past expiry, not bound) · 0–7 · 8–14 · 15–30. Tenant data only; test-flagged/synthetic data excluded. Policies in force
with no renewal started also appear ("Not started") so nothing is missed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db.tenant_safe import wrap
from app.kernel import holidays, notify, payments
from app.kernel import renewals as rn
from app.kernel.tenant_ctx import use_tenant
from app.kernel.test_data import test_client_ids

BUCKETS = (("expired", "Expired"), ("d7", "7 days or less"), ("d14", "14 days or less"), ("d30", "30 days or less"))
EXPIRED_LOOKBACK_DAYS = 90
OPEN_CLAIM_DONE = ("closed", "withdrawn")
OPEN_COMPLAINT_DONE = ("closed",)


def bucket(days: int) -> str | None:
    if days < 0:
        return "expired"
    return "d7" if days <= 7 else "d14" if days <= 14 else "d30" if days <= 30 else None


def days_to(expiry, today) -> int:
    return (rn._utc(expiry).astimezone(holidays.SYD).date() - today).days


def _days_text(d: int) -> str:
    if d < 0:
        return f"{-d} day{'s' if d != -1 else ''} overdue"
    return "Expires today" if d == 0 else f"{d} day{'s' if d != 1 else ''} left"


async def _ae(safe, client: dict, names: dict) -> dict:
    stored = client.get("assigned_ae_user_id")
    uid = await notify.active_ae(safe, stored)
    if uid:
        return {"user_id": uid, "name": names.get(uid) or "AE", "status": "ok"}
    return {"user_id": None, "name": "AE disabled" if stored else "Unassigned", "status": "disabled" if stored else "unassigned"}


async def review(store, tid: str, now: datetime | None = None, *, ae: str | None = None, stage: str | None = None,
                 insurer: str | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(holidays.SYD).date()
    lo, hi = now - timedelta(days=EXPIRED_LOOKBACK_DAYS + 1), now + timedelta(days=32)
    safe = wrap(store)
    async with use_tenant(tid):
        tclients = set(await test_client_ids(safe))
        policies = {p["policy_id"]: p async for p in safe.policies.find({"test": {"$ne": True}}, {"_id": 0})
                    if p.get("client_id") not in tclients}
        renewals = [r async for r in safe.renewals.find({"test": {"$ne": True}}, {"_id": 0})
                    if r.get("client_id") not in tclients and r.get("policy_id") in policies]
        started = {r["policy_id"] for r in renewals}
        clients = {c["client_id"]: c async for c in safe.clients.find({}, {"_id": 0, "client_id": 1, "display_name": 1, "assigned_ae_user_id": 1})}
        names = {u["user_id"]: u.get("display_name") async for u in safe.users.find({}, {"_id": 0, "user_id": 1, "display_name": 1})}
        claims = {c.get("policy_id") async for c in safe.claims.find({"stage": {"$nin": list(OPEN_CLAIM_DONE)}, "test": {"$ne": True}},
                                                                    {"policy_id": 1})}
        complaints = {m["client_id"] async for m in safe.complaints.find({"stage": {"$nin": list(OPEN_COMPLAINT_DONE)}}, {"client_id": 1})}
        payables = {p["payable_id"]: p async for p in safe.payables.find(
            {"payable_id": {"$in": [r["payable_id"] for r in renewals if r.get("payable_id")]}}, {"_id": 0})}
        items = []
        for r in renewals:
            cur = rn.cur(r)
            if cur in ("bound", "declined"):
                continue
            exp = rn._utc(r["expiry"])
            held = r.get("lapse_hold") or (r.get("refund_review") or {}).get("state") == "open"
            if not (lo <= exp <= hi or held):
                continue
            items.append(("r", r, policies[r["policy_id"]]))
        for p in policies.values():
            if p["policy_id"] in started or p.get("status") not in ("managed", "in_force") or not p.get("expiry"):
                continue
            if lo <= rn._utc(p["expiry"]) <= hi:
                items.append(("p", None, p))
        rows = []
        for kind, r, p in items:
            d = days_to((r or p)["expiry"], today)
            b = bucket(d)
            if b is None:
                continue
            c = clients.get(p["client_id"]) or {}
            who = await _ae(safe, c, names)
            cur = rn.cur(r) if r else "not_started"
            pay = payables.get((r or {}).get("payable_id")) if r else None
            money = None
            if pay:
                st = payments.state_of(pay)
                money = {"state": {"owed": "Owed", "advised": "Advised", "received": "Received", "partial": "Part received",
                                   "overpaid": "Overpaid"}[st],
                         "held_in_trust": st in ("received", "partial", "overpaid") and cur != "bound"}
            rr = (r or {}).get("refund_review") or {}
            flags = [f for f, on in (("lapse_hold", bool((r or {}).get("lapse_hold"))), ("refund_review", rr.get("state") == "open"),
                                     ("open_claim", p["policy_id"] in claims), ("open_complaint", p["client_id"] in complaints)) if on]
            rows.append({
                "bucket": b, "days": d, "days_text": _days_text(d), "renewal_id": (r or {}).get("renewal_id"), "policy_id": p["policy_id"],
                "client_id": p["client_id"], "client_name": c.get("display_name") or (r or {}).get("client_name") or "Client",
                "policy_class": p.get("product") or "Policy",
                "policy_number": p.get("policy_number") or (p.get("external") or {}).get("iadviser_policy_number") or p["policy_id"][-8:],
                "insurer": p.get("insurer_display") or "—", "expiry": rn._utc((r or p)["expiry"]).astimezone(holidays.SYD).strftime("%d/%m/%Y"),
                "stage": cur, "stage_label": "Not started" if not r else rn.LABEL[cur], "ae": who, "money": money, "flags": flags,
                "actions": {"open": f"/o/renewals?r={r['renewal_id']}" if r else None, "confirm_lapse": bool((r or {}).get("lapse_hold")),
                            "refund_or_bind": f"/o/renewals?r={r['renewal_id']}" if r and cur == "lapsed" and rr.get("state") == "open" else None,
                            "nudge": True}})
    if ae:
        rows = [x for x in rows if (x["ae"]["user_id"] == ae) or (ae == "unassigned" and x["ae"]["status"] != "ok")]
    if stage:
        rows = [x for x in rows if x["stage"] == stage]
    if insurer:
        rows = [x for x in rows if x["insurer"] == insurer]
    rows.sort(key=lambda x: x["days"])  # soonest first; expired: most overdue first
    out = [{"key": k, "label": label, "count": sum(x["bucket"] == k for x in rows), "rows": [x for x in rows if x["bucket"] == k]}
           for k, label in BUCKETS]
    return {"as_of": today.strftime("%d/%m/%Y"), "timezone": "Australia/Sydney", "buckets": out,
            "badge": out[0]["count"] + out[1]["count"]}


CSV_HEADER = ["Section", "Client", "Policy class", "Policy number", "Insurer", "Expiry", "Days", "Stage", "Assigned AE", "Money",
              "Premium held in trust", "Flags"]


def csv_rows(data: dict) -> list[list]:
    return [[b["label"], x["client_name"], x["policy_class"], x["policy_number"], x["insurer"], x["expiry"], x["days_text"],
             x["stage_label"], x["ae"]["name"], (x["money"] or {}).get("state") or "", "yes" if (x["money"] or {}).get("held_in_trust") else "",
             ", ".join(x["flags"])] for b in data["buckets"] for x in b["rows"]]
