"""R1.6 — renewal pipeline per policy term.

Upcoming → Info requested → Info received → Market → Quotes in → Recommendation ready → Client decision → Paid
(bank-confirmed) → Signed → Bound | Lapsed | Declined. Lapse risk (T-7) is a stage that remembers where it was.
Every transition is a ledger event. `tick` (durable periodic job) starts due renewals, sends reminders, flags
stuck stages, lapses and resumes signed-but-unbound renewals. Every function takes an injectable `now`.
Insurer quotes are MOCKED (or entered by an AE from a real offline quote).
"""
from __future__ import annotations

import copy
import hashlib
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from app.db.tenant_safe import wrap
from app.kernel import holidays, notify, payments
from app.kernel.advice_boundary import classify_recommendation
from app.kernel.mocks import renewal_quotes
from app.kernel.policy_field_guard import UnmatchedPolicyField, _norm, validate_requested_changes
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch
from app.kernel.trail_mongo import DuplicateIdempotencyKey

LABEL = {"upcoming": "Upcoming", "info_requested": "Info requested", "info_received": "Info received",
         "market": "Market (quotes requested)", "quotes_in": "Quotes in", "recommendation_ready": "Recommendation ready",
         "client_decision": "Client decision", "paid": "Paid (bank-confirmed)", "signed": "Signed",
         "bound": "Bound", "lapse_risk": "Lapse risk", "lapsed": "Lapsed", "declined": "Declined by client"}
CLIENT_TEXT = {"upcoming": "Your broker is getting ready for your renewal.",
               "info_requested": "Please check your details so your broker can renew your cover.",
               "info_received": "Thanks — your broker is checking your details.",
               "market": "Your broker is asking insurers for prices.", "quotes_in": "Prices are in. Your broker is comparing them.",
               "recommendation_ready": "Your broker is finishing their recommendation.",
               "client_decision": "Your renewal options are ready. Please choose one.",
               "paid": "Payment received — thank you. Please sign to finish.", "signed": "Signed. Your broker is renewing your policy.",
               "bound": "Your policy has renewed.", "lapse_risk": "Your cover ends soon and isn't renewed yet.",
               "lapsed": "Your cover has ended. Talk to your broker.", "declined": "You chose not to renew this policy."}
TERMINAL = {"bound", "lapsed", "declined"}
NEXT = {"upcoming": {"info_requested", "info_received"}, "info_requested": {"info_received"}, "info_received": {"market"},
        "market": {"quotes_in"}, "quotes_in": {"recommendation_ready"}, "recommendation_ready": {"client_decision"},
        "client_decision": {"paid", "declined"}, "paid": {"signed", "client_decision"}, "signed": {"bound"}}
STUCK_BD = {"upcoming": 2, "info_requested": 10, "info_received": 2, "market": 5, "quotes_in": 2,
            "recommendation_ready": 2, "client_decision": 5, "paid": 2, "signed": 1}
REMINDERS = (45, 30, 21)
LAPSE_RISK_DAYS = 7
GENERAL_ADVICE_WARNING = ("General advice warning: this comparison doesn't take into account your objectives, financial situation "
                          "or needs. Read the policy wording before you decide. (Wording to be finalised in Phase 8.)")


class Bad(ValueError):
    pass


def _utc(dt):
    if dt is None:
        return None
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _now(now):
    return _utc(now) or datetime.now(timezone.utc)


def cur(r: dict) -> str:
    return r.get("resume_stage") or r["stage"] if r["stage"] == "lapse_risk" else r["stage"]


def _clean(d):
    if d:
        d.pop("_id", None)
    return d


async def _event(store, tid, typ, payload, idem, by="system", role="renewals", mocked=False):
    try:
        await append_via_dispatch(store, tid, typ, actor={"kind": "human" if by != "system" else "system", "id": by, "name": role},
                                  payload=payload, idempotency_key=idem, mocked=mocked)
    except DuplicateIdempotencyKey:
        pass


async def get(store, tid, renewal_id) -> dict:
    async with use_tenant(tid):
        r = await wrap(store).renewals.find_one({"renewal_id": renewal_id}, {"_id": 0})
    if not r:
        raise LookupError("renewal_not_found")
    return r


async def _policy(store, tid, policy_id) -> dict:
    async with use_tenant(tid):
        return await wrap(store).policies.find_one({"policy_id": policy_id}, {"_id": 0}) or {}


# ---- alerts ----
async def _alert(store, tid, r, who, kind, title, body, idem, link=None):
    safe = wrap(store)
    async with use_tenant(tid):
        if who == "ae":
            ae = await notify.assigned_ae(safe, r["client_id"])
            to = [ae] if ae else await notify._staff(safe, ("ae",))
        elif who == "principal":
            to = await notify._staff(safe, ("principal",))
        else:
            to = await notify._members(safe, r["client_id"], None)
    link = link or (f"/o/renewals?r={r['renewal_id']}" if who != "client" else f"/c/renewal/{r['renewal_id']}")
    for uid in to:
        await notify.deliver(store, tid, uid, kind=kind, title=title, many=title, body=body, link=link,
                             dedupe_key=f"{kind}:{r['renewal_id']}", idem=idem, test=bool(r.get("test")))


async def _move(store, tid, r, to, *, by="system", role="renewals", now=None, note=None, **extra) -> dict:
    now, frm = _now(now), r["stage"]
    if to not in ("lapse_risk", "lapsed") and to not in NEXT.get(cur(r), set()):
        raise Bad(f"Can't move a renewal from {LABEL[cur(r)]} to {LABEL[to]}.")
    n = len(r.get("history") or [])
    h = {"from": frm, "to": to, "at": now, "by": by, "note": note}
    sets = {"stage": to, "stage_since": now, "updated_at": now, **extra}
    if to == "lapse_risk":
        sets["resume_stage"] = cur(r)
    elif frm == "lapse_risk":
        sets["resume_stage"] = None
    async with use_tenant(tid):
        res = await wrap(store).renewals.update_one({"renewal_id": r["renewal_id"], "stage": frm},
                                                    {"$set": sets, "$push": {"history": h}})
    if not res.modified_count:
        raise Bad("This renewal changed in the meantime. Refresh and try again.")
    r.update(sets)
    r.setdefault("history", []).append(h)
    await _event(store, tid, "RenewalStageChanged", {"renewal_id": r["renewal_id"], "envelope_id": r["envelope_id"],
                                                     "policy_id": r["policy_id"], "client_id": r["client_id"], "from": frm, "to": to,
                                                     "at": now.isoformat(), "note": note, "test": bool(r.get("test"))},
                 f"renewal:{r['renewal_id']}:{n}:{to}", by, role)
    await _alert(store, tid, r, "ae", "renewal_stage", f"Renewal {LABEL[to].lower()}: {r.get('client_name', 'a client')}",
                 f"{r.get('product') or 'Policy'} — now {LABEL[to]}.", f"renewal_stage:{r['renewal_id']}:{n}")
    if to in ("info_requested", "client_decision", "bound", "lapsed"):
        await _alert(store, tid, r, "client", f"renewal_{to}", {"info_requested": "Renewal: please check your details",
                     "client_decision": "Your renewal options are ready", "bound": "Your policy has renewed",
                     "lapsed": "Your cover has ended"}[to], CLIENT_TEXT[to], f"renewal_client:{r['renewal_id']}:{to}")
    return r


# ---- start ----
def checklist(policy: dict) -> list[dict]:
    s = policy.get("schedule") or {}
    items = []
    if s.get("vehicles"):
        items.append({"key": "vehicles", "kind": "vehicles", "question": "Are these still the vehicles to insure?",
                      "current": [{"rego": v.get("rego"), "label": " ".join(str(v[k]) for k in ("year", "make", "model") if v.get(k))}
                                  for v in s["vehicles"]]})
    if s.get("addresses"):
        items.append({"key": "address", "kind": "address", "question": "Is this still your business address?",
                      "current": s["addresses"][0] if isinstance(s["addresses"][0], str) else s["addresses"][0].get("address")})
    items += [{"key": "turnover", "kind": "money", "question": "Expected turnover for the next 12 months (A$)",
               "current": s.get("turnover")},
              {"key": "claims", "kind": "claims", "question": "Any claims or incidents since last year?"},
              {"key": "changes", "kind": "text", "question": "Anything else that has changed in your business?"}]
    return items


async def start(store, tid, policy: dict, *, now=None, by="system", role="renewals") -> dict:
    now = _now(now)
    safe = wrap(store)
    async with use_tenant(tid):
        existing = await safe.renewals.find_one({"policy_id": policy["policy_id"]}, {"_id": 0})
        if existing:
            return existing
        client = await safe.clients.find_one({"client_id": policy["client_id"]}) or {}
    exp = _utc(policy["expiry"])
    r = {"renewal_id": str(uuid.uuid4()), "envelope_id": str(uuid.uuid4()), "policy_id": policy["policy_id"],
         "client_id": policy["client_id"], "client_name": client.get("display_name"), "product": policy.get("product"),
         "insurer_display": policy.get("insurer_display"), "expiring_premium": str(policy.get("premium_amount") or 0),
         "expiry": exp, "stage": "upcoming", "resume_stage": None, "started_at": now, "stage_since": now, "updated_at": now,
         "checklist": checklist(policy), "answers": None, "accepted_changes": [], "issues": [], "needs_review": False,
         "quotes": [], "recommendation": None, "decision": None, "payable_id": None, "signature": None,
         "new_policy_id": None, "certificate_id": None, "reminders": [], "lapse_alerted": False, "history": [],
         "test": bool(policy.get("test") or client.get("test"))}
    async with use_tenant(tid):
        try:
            await safe.renewals.insert_one(dict(r))
        except Exception:  # unique (tenant, policy) — someone else started it
            return await safe.renewals.find_one({"policy_id": policy["policy_id"]}, {"_id": 0})
    await _event(store, tid, "RenewalStarted", {"renewal_id": r["renewal_id"], "envelope_id": r["envelope_id"],
                                                "policy_id": r["policy_id"], "client_id": r["client_id"], "expiry": exp.isoformat(),
                                                "started_at": now.isoformat(), "test": r["test"]}, f"renewal_start:{r['renewal_id']}", by, role)
    return r


async def request_info(store, tid, r, *, by, role, now=None) -> dict:
    return await _move(store, tid, r, "info_requested", by=by, role=role, now=now, info_requested_at=_now(now))


# ---- info ----
def _money(v):
    try:
        d = Decimal(str(v).replace(",", "").replace("$", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return d if 0 < d < Decimal("1e11") else None


def assess_answers(policy: dict, answers: dict) -> tuple[list[dict], list[str], dict]:
    """Changes go through policy_field_guard; anything that doesn't fit is a broker issue, never a guess."""
    changes, issues, facts = [], [], {}
    a = answers or {}
    v = a.get("vehicles") or {}
    if v and not v.get("confirmed", True):
        for rego in v.get("remove") or []:
            changes.append({"field_path": "schedule.vehicles[]", "operation": "remove", "from_value": {"rego": str(rego)}})
        for add in v.get("add") or []:
            if not (isinstance(add, dict) and str(add.get("rego") or "").strip()):
                issues.append("A new vehicle has no registration.")
                continue
            changes.append({"field_path": "schedule.vehicles[]", "operation": "add",
                            "to_value": {k: add[k] for k in ("rego", "make", "model", "year") if add.get(k)}})
    ad = a.get("address") or {}
    if ad and not ad.get("confirmed", True):
        new = str(ad.get("new_address") or "").strip()
        held = next((i["current"] for i in checklist(policy) if i["key"] == "address"), None)
        if not new:
            issues.append("The client said the address changed but didn't give the new one.")
        else:
            changes.append({"field_path": "schedule.addresses[0]", "operation": "set", "from_value": held, "to_value": new})
    ok = []
    for ch in changes:
        try:
            validate_requested_changes(policy, [ch])
            ok.append(ch)
        except UnmatchedPolicyField as e:
            issues.append(f"Doesn't match the policy: {e.reason} ({e.attempted}).")
    t = (a.get("turnover") or {}).get("value")
    if t not in (None, ""):
        if (m := _money(t)) is None:
            issues.append(f"Turnover '{str(t)[:40]}' isn't an amount we can use.")
        else:
            facts["turnover"] = str(m)
    c = a.get("claims") or {}
    if c.get("any"):
        issues.append(f"Client reported claims or incidents: {str(c.get('details') or 'no details')[:300]}")
    if str((a.get("changes") or {}).get("text") or "").strip():
        issues.append(f"Client described other changes: {str(a['changes']['text'])[:300]}")
    return ok, issues, facts


async def submit_info(store, tid, r, answers: dict, *, by, role, now=None) -> dict:
    if cur(r) != "info_requested":
        raise Bad("Your broker isn't waiting for details on this renewal.")
    policy = await _policy(store, tid, r["policy_id"])
    ok, issues, facts = assess_answers(policy, answers)
    sha = hashlib.sha256(repr(sorted((answers or {}).items())).encode()).hexdigest()
    async with use_tenant(tid):
        await wrap(store).renewals.update_one({"renewal_id": r["renewal_id"]}, {"$set": {
            "answers": answers, "accepted_changes": ok, "issues": issues, "facts": facts, "needs_review": bool(issues),
            "info_submitted_by": by}})
    r.update(answers=answers, accepted_changes=ok, issues=issues, facts=facts, needs_review=bool(issues))
    await _event(store, tid, "RenewalInfoSubmitted", {"renewal_id": r["renewal_id"], "envelope_id": r["envelope_id"],
                                                      "policy_id": r["policy_id"], "answers_sha256": sha, "changes": len(ok),
                                                      "issues": len(issues), "test": bool(r.get("test"))},
                 f"renewal_info:{r['renewal_id']}:{sha[:16]}", by, role)
    return await _move(store, tid, r, "info_received", by=by, role=role, now=now,
                       note="needs broker review" if issues else None)


async def accept_info(store, tid, r, note, *, by, role, now=None) -> dict:
    """AE clears the review (or records details confirmed offline)."""
    if cur(r) == "upcoming" or cur(r) == "info_requested":
        if not (note or "").strip():
            raise Bad("Say how the details were confirmed (e.g. 'confirmed by phone').")
        r = await _move(store, tid, r, "info_received", by=by, role=role, now=now, note=note)
    elif cur(r) != "info_received":
        raise Bad("The details are already accepted.")
    async with use_tenant(tid):
        await wrap(store).renewals.update_one({"renewal_id": r["renewal_id"]}, {"$set": {"needs_review": False,
                                                                                       "review_note": note, "reviewed_by": by}})
    r.update(needs_review=False, review_note=note)
    return r


# ---- market ----
async def insurers(store, tid) -> list[str]:
    safe = wrap(store)
    async with use_tenant(tid):
        names = {i.get("name") async for i in safe.insurers.find({}, {"name": 1})}
        names |= {p.get("insurer_display") async for p in safe.policies.find({}, {"insurer_display": 1})}
    return sorted(n for n in names if n)


async def _to_market(store, tid, r, by, role, now):
    if cur(r) == "info_received":
        if r.get("needs_review"):
            raise Bad("The client's details need a broker review first.")
        r = await _move(store, tid, r, "market", by=by, role=role, now=now)
    elif cur(r) not in ("market", "quotes_in", "recommendation_ready"):
        raise Bad(f"Quotes can't be added at {LABEL[cur(r)]}.")
    return r


async def _add_quote(store, tid, r, q, by, role, now):
    q = {"quote_id": str(uuid.uuid4()), **q, "at": _now(now), "by": by}
    async with use_tenant(tid):
        await wrap(store).renewals.update_one({"renewal_id": r["renewal_id"]}, {"$push": {"quotes": q}})
    r.setdefault("quotes", []).append(q)
    await _event(store, tid, "RenewalQuoteRecorded", {"renewal_id": r["renewal_id"], "envelope_id": r["envelope_id"],
                                                      "policy_id": r["policy_id"], "quote_id": q["quote_id"], "insurer": q["insurer"],
                                                      "premium": q["premium"], "excess": q["excess"], "data_basis": q["data_basis"],
                                                      "mocked": q["mocked"], "test": bool(r.get("test"))},
                 f"renewal_quote:{q['quote_id']}", by, role, mocked=q["mocked"])
    if cur(r) == "market":
        r = await _move(store, tid, r, "quotes_in", by=by, role=role, now=now)
    return r


async def request_quotes(store, tid, r, names: list[str], *, by, role, now=None) -> dict:
    if await holidays.quoting_mode(store, tid) == "manual_only":
        raise Bad("This brokerage uses real insurer quotes only. Enter the quote you received from the insurer.")
    names = list(dict.fromkeys(n.strip() for n in names if n and n.strip()))
    if not 1 <= len(names) <= renewal_quotes.MAX_INSURERS:
        raise Bad("Pick 1 to 3 insurers.")
    known = set(await insurers(store, tid))
    if any(n not in known for n in names):
        raise Bad("Pick insurers from the list.")
    mocked = [q for q in r.get("quotes") or [] if q.get("mocked")]
    if len(mocked) + len(names) > renewal_quotes.MAX_INSURERS:
        raise Bad("Up to 3 mocked insurer quotes per renewal.")
    r = await _to_market(store, tid, r, by, role, now)
    policy = await _policy(store, tid, r["policy_id"])
    exc = ((policy.get("schedule") or {}).get("excess") or {}).get("any_one_occurrence")
    for n in names:
        q = renewal_quotes.quote(n, r["expiring_premium"], exc, r["renewal_id"])
        r = await _add_quote(store, tid, r, {**q, "limits": (policy.get("schedule") or {}).get("limits") or {}}, by, role, now)
    return r


async def manual_quote(store, tid, r, body: dict, *, by, by_name, role, now=None) -> dict:
    ins = str(body.get("insurer") or "").strip()[:80]
    prem, exc = _money(body.get("premium")), _money(body.get("excess") or 0) if body.get("excess") not in (None, "", 0, "0") else Decimal(0)
    if not ins or prem is None or exc is None:
        raise Bad("Give the insurer, the premium and the excess as amounts.")
    policy = await _policy(store, tid, r["policy_id"])
    r = await _to_market(store, tid, r, by, role, now)
    terms = [str(t).strip()[:200] for t in (body.get("key_terms") or []) if str(t).strip()][:8]
    return await _add_quote(store, tid, r, {"insurer": ins, "premium": str(prem.quantize(Decimal("0.01"))), "excess": str(exc),
                                            "key_terms": terms, "limits": body.get("limits") or (policy.get("schedule") or {}).get("limits") or {},
                                            "data_basis": f"manual entry by {by_name}", "mocked": False}, by, role, now)


def comparison(r: dict, policy: dict) -> dict:
    s = policy.get("schedule") or {}
    old_p, old_e = Decimal(r["expiring_premium"]), Decimal(str((s.get("excess") or {}).get("any_one_occurrence") or 0))
    rows = []
    for q in r.get("quotes") or []:
        p, e = Decimal(q["premium"]), Decimal(q["excess"])
        diffs = [t for t in q.get("key_terms") or []]
        if q.get("limits") and q["limits"] != (s.get("limits") or {}):
            diffs.insert(0, "Limits differ from expiring")
        rows.append({"quote_id": q["quote_id"], "insurer": q["insurer"], "premium": q["premium"],
                     "premium_change": str(p - old_p), "premium_change_pct": str(((p - old_p) / old_p * 100).quantize(Decimal("0.1"))) if old_p else None,
                     "excess": q["excess"], "excess_change": str(e - old_e), "limits": q.get("limits") or {},
                     "limits_same": (q.get("limits") or {}) == (s.get("limits") or {}), "key_differences": diffs,
                     "data_basis": q["data_basis"], "mocked": q["mocked"]})
    return {"expiring": {"insurer": r.get("insurer_display"), "premium": r["expiring_premium"], "excess": str(old_e),
                         "limits": s.get("limits") or {}, "expiry": _utc(r["expiry"]).isoformat()}, "options": rows}


# ---- recommendation ----
async def recommend(store, tid, r, quote_id, text, reasons, *, by, role, now=None) -> dict:
    if cur(r) not in ("quotes_in", "recommendation_ready"):
        raise Bad("A recommendation needs quotes in first.")
    q = next((q for q in r.get("quotes") or [] if q["quote_id"] == quote_id), None)
    reasons = [str(x).strip()[:300] for x in reasons or [] if str(x).strip()][:6]
    if not q or not (text or "").strip() or not reasons:
        raise Bad("Pick the recommended option and write the recommendation with at least one reason.")
    policy = await _policy(store, tid, r["policy_id"])
    verdict = classify_recommendation(f"{text} {' '.join(reasons)}", q, policy)
    rec = {"quote_id": quote_id, "text": text.strip()[:2000], "reasons": reasons, **verdict, "by": by, "at": _now(now),
           "principal_approved_by": None, "principal_approved_at": None, "released_at": None,
           "general_advice_warning": GENERAL_ADVICE_WARNING}
    async with use_tenant(tid):
        await wrap(store).renewals.update_one({"renewal_id": r["renewal_id"]}, {"$set": {"recommendation": rec}})
    r["recommendation"] = rec
    await _event(store, tid, "RenewalRecommendationWritten", {"renewal_id": r["renewal_id"], "envelope_id": r["envelope_id"],
                 "policy_id": r["policy_id"], "quote_id": quote_id, "tier": verdict["tier"], "rule_id": verdict["rule_id"],
                 "text_sha256": hashlib.sha256(rec["text"].encode()).hexdigest(), "test": bool(r.get("test"))},
                 f"renewal_rec:{r['renewal_id']}:{rec['at'].isoformat()}", by, role)
    if verdict["tier"] == "P":
        await _alert(store, tid, r, "principal", "renewal_advice", f"Renewal advice needs your approval: {r.get('client_name')}",
                     verdict["reason"], f"renewal_advice:{r['renewal_id']}:{rec['at'].isoformat()}")
    if cur(r) == "quotes_in":
        r = await _move(store, tid, r, "recommendation_ready", by=by, role=role, now=now)
    return r


async def approve_advice(store, tid, r, *, by, now=None) -> dict:
    rec = r.get("recommendation")
    if not rec or rec["tier"] != "P":
        raise Bad("There's no personal-advice recommendation waiting for approval.")
    if rec["by"] == by:
        raise Bad("A different person must approve personal advice.")
    async with use_tenant(tid):
        await wrap(store).renewals.update_one({"renewal_id": r["renewal_id"]}, {"$set": {
            "recommendation.principal_approved_by": by, "recommendation.principal_approved_at": _now(now)}})
    rec.update(principal_approved_by=by, principal_approved_at=_now(now))
    await _event(store, tid, "RenewalAdviceApproved", {"renewal_id": r["renewal_id"], "envelope_id": r["envelope_id"],
                 "policy_id": r["policy_id"], "rule_id": rec["rule_id"], "test": bool(r.get("test"))},
                 f"renewal_advice_ok:{r['renewal_id']}:{rec['at'].isoformat() if isinstance(rec['at'], datetime) else rec['at']}", by, "principal")
    return r


def released(rec: dict | None) -> bool:
    return bool(rec and rec.get("released_at"))


async def send_to_client(store, tid, r, *, by, role, now=None) -> dict:
    rec = r.get("recommendation")
    if cur(r) != "recommendation_ready" or not rec:
        raise Bad("Write the recommendation first.")
    if rec["tier"] == "P" and not rec.get("principal_approved_by"):
        raise Bad("This is personal advice: a Principal must approve it before the client sees it.")
    async with use_tenant(tid):
        await wrap(store).renewals.update_one({"renewal_id": r["renewal_id"]}, {"$set": {"recommendation.released_at": _now(now)}})
    rec["released_at"] = _now(now)
    return await _move(store, tid, r, "client_decision", by=by, role=role, now=now)


# ---- client ----
async def decide(store, tid, r, choice: str, reason: str | None, *, by, role, now=None) -> dict:
    if cur(r) != "client_decision" or not released(r.get("recommendation")):
        raise Bad("There's nothing to decide on this renewal right now.")
    if r.get("decision"):
        raise Bad("You've already chosen.")
    if choice == "decline":
        dec = {"choice": "decline", "reason": (reason or "")[:500], "by": by, "at": _now(now)}
    else:
        q = next((q for q in r.get("quotes") or [] if q["quote_id"] == choice), None)
        if not q:
            raise Bad("Pick one of the options shown.")
        dec = {"choice": choice, "insurer": q["insurer"], "premium": q["premium"], "by": by, "at": _now(now)}
    async with use_tenant(tid):
        res = await wrap(store).renewals.update_one({"renewal_id": r["renewal_id"], "decision": None}, {"$set": {"decision": dec}})
    if not res.modified_count:
        raise Bad("You've already chosen.")
    r["decision"] = dec
    await _event(store, tid, "RenewalClientDecision", {"renewal_id": r["renewal_id"], "envelope_id": r["envelope_id"],
                 "policy_id": r["policy_id"], "client_id": r["client_id"], "choice": dec["choice"], "premium": dec.get("premium"),
                 "explicit_approval": True, "authority": "can_approve", "test": bool(r.get("test"))},
                 f"renewal_decision:{r['renewal_id']}", by, role)
    if choice == "decline":
        return await _move(store, tid, r, "declined", by=by, role=role, now=now, note=dec["reason"] or None)
    p = await payments.ensure_payable(store, tid, envelope_id=r["envelope_id"], policy_id=r["policy_id"], amount=dec["premium"],
                                      kind="renewal_premium")
    async with use_tenant(tid):
        await wrap(store).renewals.update_one({"renewal_id": r["renewal_id"]}, {"$set": {"payable_id": p["payable_id"]}})
    r["payable_id"] = p["payable_id"]
    return r


async def payable(store, tid, r) -> dict | None:
    if not r.get("payable_id"):
        return None
    async with use_tenant(tid):
        return _clean(await wrap(store).payables.find_one({"payable_id": r["payable_id"]}))


async def advise_paid(store, tid, r, *, by, role) -> dict:
    p = await payable(store, tid, r)
    if not p:
        raise Bad("Choose an option first.")
    await _event(store, tid, "payment.advised", {"envelope_id": r["envelope_id"], "renewal_id": r["renewal_id"],
                 "policy_id": r["policy_id"], "amount": p["amount"], "currency": p["currency"], "crn": p["crn"],
                 "test": bool(r.get("test"))}, f"payment:advised:{r['envelope_id']}", by, role)
    await payments.mark_advised(store, tid, r["envelope_id"])  # Advised only — Received comes from the bank feed
    return await payable(store, tid, r)


async def on_received(store, tid, p: dict) -> None:
    """Bank feed hook: a renewal premium fully received moves Client decision → Paid."""
    if p.get("kind") != "renewal_premium" or p.get("state") != "received":
        return
    async with use_tenant(tid):
        r = await wrap(store).renewals.find_one({"envelope_id": p["envelope_id"]}, {"_id": 0})
    if r and cur(r) == "client_decision" and r.get("decision"):
        await _move(store, tid, r, "paid", by="system", role="bank-feed", note=f"CRN {p['crn']} received")


async def on_reversed(store, tid, payable_id: str) -> None:
    async with use_tenant(tid):
        r = await wrap(store).renewals.find_one({"payable_id": payable_id}, {"_id": 0})
    if r and cur(r) == "paid":
        await _move(store, tid, r, "client_decision", by="system", role="bank-feed", note="bank receipt unmatched")


async def sign(store, tid, r, *, by, role, now=None) -> dict:
    from app.kernel.mocks import e_sign
    if cur(r) != "paid":
        raise Bad("Signing opens once your payment is confirmed by the bank.")
    if await holidays.quoting_mode(store, tid) == "manual_only":
        raise Bad("Signing in the app isn't switched on for this brokerage yet. Your broker will arrange signing with you.")
    req = await e_sign.request_signature(envelope_id=r["envelope_id"], document_ref=f"renewal:{r['renewal_id']}",
                                         signer_email=f"{by}@example.test")
    sig = await e_sign.simulate_signature(envelope_id=r["envelope_id"], request_id=req["request_id"])
    await _event(store, tid, "RenewalSigned", {"renewal_id": r["renewal_id"], "envelope_id": r["envelope_id"],
                 "policy_id": r["policy_id"], "signature_id": sig["signature_id"], "signature_blob_hash": sig["signature_blob_hash"],
                 "mocked": True, "test": bool(r.get("test"))}, f"renewal_sign:{r['renewal_id']}", by, role, mocked=True)
    r = await _move(store, tid, r, "signed", by=by, role=role, now=now, signature={"signature_id": sig["signature_id"], "mocked": True})
    return await bind(store, tid, r, now=now)


# ---- bind ----
def _plus_year(d: datetime) -> datetime:
    try:
        return d.replace(year=d.year + 1)
    except ValueError:
        return d.replace(year=d.year + 1, day=28)


def apply_changes(schedule: dict, changes: list[dict], facts: dict) -> dict:
    s = copy.deepcopy(schedule or {})
    for ch in changes or []:
        if ch["field_path"] == "schedule.vehicles[]" and ch["operation"] == "remove":
            want = _norm((ch.get("from_value") or {}).get("rego"))
            s["vehicles"] = [v for v in s.get("vehicles") or [] if _norm(v.get("rego")) != want]
        elif ch["field_path"] == "schedule.vehicles[]" and ch["operation"] == "add":
            s.setdefault("vehicles", []).append(dict(ch["to_value"]))
        elif ch["field_path"] == "schedule.addresses[0]":
            s["addresses"] = [ch["to_value"]] + list((s.get("addresses") or [])[1:])
    if facts.get("turnover"):
        s["turnover"] = float(facts["turnover"])
    return s


async def bind(store, tid, r, *, now=None) -> dict:
    """Idempotent: the new term, the certificate and the change packet each happen once (resumable from tick)."""
    from app.kernel.certificate import regenerate_certificate_dispatch
    if cur(r) != "signed":
        return r
    safe = wrap(store)
    old = await _policy(store, tid, r["policy_id"])
    q = next(q for q in r["quotes"] if q["quote_id"] == r["decision"]["choice"])
    new_id = r.get("new_policy_id") or str(uuid.uuid4())
    start_at = _utc(old["expiry"])
    end_at = _plus_year(start_at)
    async with use_tenant(tid):
        if not await safe.policies.find_one({"policy_id": new_id}):
            schedule = apply_changes(old.get("schedule"), r.get("accepted_changes"), r.get("facts") or {})
            schedule["excess"] = {**(schedule.get("excess") or {}), "any_one_occurrence": float(q["excess"]), "currency": "AUD"}
            if q.get("limits"):
                schedule["limits"] = q["limits"]
            doc = {"policy_id": new_id, "client_id": old["client_id"], "status": "managed", "product": old.get("product"),
                   "insurer_display": q["insurer"], "inception": start_at, "expiry": end_at, "premium_amount": float(q["premium"]),
                   "premium_currency": "AUD", "premium_status": "current", "schedule": schedule, "imported_from": None,
                   "renewed_from": old["policy_id"], "renewal_id": r["renewal_id"], "quote_data_basis": q["data_basis"],
                   "prior_external": old.get("external"), "test": bool(old.get("test")), "fictional": bool(old.get("fictional")),
                   "created_at": _now(now)}
            await safe.policies.insert_one(dict(doc))
            await safe.renewals.update_one({"renewal_id": r["renewal_id"]}, {"$set": {"new_policy_id": new_id}})
            await safe.policies.update_one({"policy_id": old["policy_id"]}, {"$set": {"status": "renewed", "renewed_to": new_id}})
            r["new_policy_id"] = new_id
            await _event(store, tid, "PolicyCreated", {"policy_id": new_id, "client_id": old["client_id"], "status": "managed",
                         "product": doc["product"], "insurer_display": doc["insurer_display"], "premium_amount": doc["premium_amount"],
                         "premium_currency": "AUD", "schedule": schedule, "inception": start_at.isoformat(), "expiry": end_at.isoformat(),
                         "renewed_from": old["policy_id"], "renewal_id": r["renewal_id"], "data_basis": q["data_basis"],
                         "mocked": q["mocked"], "test": doc["test"]}, f"renewal_policy:{r['renewal_id']}", mocked=q["mocked"])
    token = None
    if not r.get("certificate_id"):
        cert = await regenerate_certificate_dispatch(store=store, tenant_id=tid, policy_id=new_id, envelope_id=r["envelope_id"])
        token = cert["raw_token"]
        async with use_tenant(tid):
            await safe.renewals.update_one({"renewal_id": r["renewal_id"]}, {"$set": {"certificate_id": cert["certificate"]["certificate_id"]}})
        r["certificate_id"] = cert["certificate"]["certificate_id"]
    pk = packet(r, old, await payable(store, tid, r))
    await _event(store, tid, "RenewalChangePacketIssued", {"renewal_id": r["renewal_id"], "envelope_id": r["envelope_id"],
                 "policy_id": new_id, "packet_sha256": hashlib.sha256(pk["text"].encode()).hexdigest(), "write_back": False,
                 "test": bool(r.get("test"))}, f"renewal_packet:{r['renewal_id']}")
    await _event(store, tid, "RenewalBound", {"renewal_id": r["renewal_id"], "envelope_id": r["envelope_id"],
                 "old_policy_id": old["policy_id"], "new_policy_id": new_id, "period_from": start_at.isoformat(),
                 "period_to": end_at.isoformat(), "premium": q["premium"], "insurer": q["insurer"], "data_basis": q["data_basis"],
                 "test": bool(r.get("test"))}, f"renewal_bound:{r['renewal_id']}", mocked=q["mocked"])
    r = await _move(store, tid, r, "bound", now=now)
    r["verifier_token"] = token
    return r


def packet(r: dict, old: dict, pay: dict | None) -> dict:
    q = next((q for q in r.get("quotes") or [] if q["quote_id"] == (r.get("decision") or {}).get("choice")), {})
    start_at = _utc(old.get("expiry"))
    ext = old.get("external") or old.get("prior_external") or {}
    lines = [("Client", r.get("client_name")), ("iAdviser policy number (expiring)", ext.get("iadviser_policy_number") or "—"),
             ("Class", r.get("product")), ("New period", f"{start_at:%d/%m/%Y} to {_plus_year(start_at):%d/%m/%Y}" if start_at else "—"),
             ("Insurer", q.get("insurer")), ("Premium (A$)", f"{q.get('premium')} — {q.get('data_basis')}"),
             ("Excess (A$)", q.get("excess")), ("Payment", f"CRN {pay['crn']} — {pay['state']}" if pay else "—")]
    for ch in r.get("accepted_changes") or []:
        val = ch.get("to_value") or ch.get("from_value")
        lines.append(("Schedule change", f"{ch['operation']} {ch['field_path']}: {val.get('rego') if isinstance(val, dict) else val}"))
    text = "brokAId renewal change packet — key these into iAdviser by hand (no write-back)\n" + "\n".join(f"{k}: {v}" for k, v in lines)
    return {"lines": [{"label": k, "value": v} for k, v in lines], "text": text, "write_back": False}


# ---- periodic ----
async def tick(store, tid, now=None, policy_ids: list[str] | None = None) -> dict:
    now = _now(now)
    cfg = await holidays.tenant_settings(store, tid)
    st, lead = cfg["holiday_state"], cfg["renewal_lead_days"]
    safe, made = wrap(store), {"started": 0, "reminders": 0, "stuck": 0, "lapse_risk": 0, "lapsed": 0, "resumed": 0}
    q = {"status": "managed", "expiry": {"$gt": now, "$lte": now + timedelta(days=lead)}, "archived_at": {"$in": [None]}}
    if policy_ids is not None:
        q["policy_id"] = {"$in": policy_ids}
    else:
        q["test"] = {"$ne": True}
    async with use_tenant(tid):
        due = [p async for p in safe.policies.find(q, {"_id": 0})]
        have = {r["policy_id"] async for r in safe.renewals.find({"policy_id": {"$in": [p["policy_id"] for p in due]}}, {"policy_id": 1})}
    for p in due:
        if p["policy_id"] not in have:
            await start(store, tid, p, now=now)
            made["started"] += 1
    rq = {"stage": {"$nin": list(TERMINAL)}}
    if policy_ids is not None:
        rq["policy_id"] = {"$in": policy_ids}
    async with use_tenant(tid):
        rows = [r async for r in safe.renewals.find(rq, {"_id": 0})]
    today = now.astimezone(holidays.SYD).date()
    for r in rows:
        exp = _utc(r["expiry"])
        if cur(r) == "signed":
            await bind(store, tid, r, now=now)
            made["resumed"] += 1
            continue
        if now >= exp:
            await _move(store, tid, r, "lapsed", now=now, note="not bound by expiry")
            async with use_tenant(tid):
                await safe.policies.update_one({"policy_id": r["policy_id"], "status": "managed"}, {"$set": {"status": "lapsed"}})
            await _event(store, tid, "PolicyLapsed", {"policy_id": r["policy_id"], "renewal_id": r["renewal_id"],
                         "expiry": exp.isoformat(), "test": bool(r.get("test"))}, f"policy_lapsed:{r['policy_id']}")
            await _alert(store, tid, r, "principal", "renewal_lapsed", f"Policy lapsed: {r.get('client_name')}",
                         f"{r.get('product')} expired on {exp.astimezone(holidays.SYD):%d/%m/%Y} without renewal.",
                         f"renewal_lapsed_p:{r['renewal_id']}")
            made["lapsed"] += 1
            continue
        if now >= exp - timedelta(days=LAPSE_RISK_DAYS) and not r.get("lapse_alerted"):
            async with use_tenant(tid):
                await safe.renewals.update_one({"renewal_id": r["renewal_id"]}, {"$set": {"lapse_alerted": True}})
            await _move(store, tid, r, "lapse_risk", now=now, note=f"T-{LAPSE_RISK_DAYS}, not bound")
            body = f"{r.get('product')} ends on {exp.astimezone(holidays.SYD):%d/%m/%Y} and isn't renewed yet."
            for who in ("principal", "client"):
                await _alert(store, tid, r, who, "renewal_lapse_risk", "Lapse risk" if who != "client" else "Your cover ends soon",
                             body, f"renewal_lapse_risk:{who}:{r['renewal_id']}")
            made["lapse_risk"] += 1
        if cur(r) == "info_requested" and not holidays.in_quiet_hours(now):
            asked = _utc(r.get("info_requested_at") or r["stage_since"]).astimezone(holidays.SYD).date()
            for mark in REMINDERS:
                day = holidays.on_or_before((exp - timedelta(days=mark)).astimezone(holidays.SYD).date(), st)
                if mark in (r.get("reminders") or []) or not (asked < day <= today) or not holidays.is_business_day(today, st):
                    continue
                await _alert(store, tid, r, "client", "renewal_reminder", "Reminder: please check your renewal details",
                             f"Your {r.get('product') or 'policy'} renews on {exp.astimezone(holidays.SYD):%d/%m/%Y}.",
                             f"renewal_reminder:{r['renewal_id']}:{mark}")
                async with use_tenant(tid):
                    await safe.renewals.update_one({"renewal_id": r["renewal_id"]}, {"$addToSet": {"reminders": mark}})
                made["reminders"] += 1
                break
        if is_stuck(r, now, st):
            since = _utc(r["stage_since"])
            await _alert(store, tid, r, "ae", "renewal_stuck", f"Renewal stuck at {LABEL[r['stage']]}: {r.get('client_name')}",
                         f"No progress for {holidays.business_days_between(since.date(), now.date(), st)} business days.",
                         f"renewal_stuck:{r['renewal_id']}:{r['stage']}:{since.isoformat()}")
            made["stuck"] += 1
    return made


def is_stuck(r: dict, now: datetime, state: str) -> bool:
    limit = STUCK_BD.get(r["stage"])
    return bool(limit) and holidays.business_days_between(_utc(r["stage_since"]).date(), now.date(), state) > limit


async def ensure_indexes(store) -> None:
    # tenant-safe: system-only — index DDL
    await store.renewals.create_index([("tenant_id", 1), ("policy_id", 1)], unique=True)
    await store.renewals.create_index([("tenant_id", 1), ("renewal_id", 1)], unique=True)
    await store.renewals.create_index([("tenant_id", 1), ("envelope_id", 1)])


def day(dt) -> date | None:
    return _utc(dt).astimezone(holidays.SYD).date() if dt else None
