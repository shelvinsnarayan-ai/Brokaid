"""R1.7 — complaints (IDR, RG 271) and privacy requests (APP 12/13): one clock + register engine, two kinds.

Complaint: acknowledge within 1 business day (holiday calendar), IDR response due 30 calendar days from receipt.
Privacy: response due 30 calendar days. Alerts to the assigned AE + Principal at 5 and 2 days before due and when overdue.
Ledger events carry ids, stages and hashes only — the content stays in the register (Principal / assigned staff / client).
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from app.db.tenant_safe import wrap
from app.kernel import holidays, notify
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch
from app.kernel.trail_mongo import DuplicateIdempotencyKey

KIND = {
    "complaint": {"coll": "complaints", "event": "ComplaintStageChanged", "label": "Complaint",
                  "stages": ["received", "acknowledged", "investigating", "response_sent", "closed", "escalated_afca"],
                  "next": {"received": {"acknowledged"}, "acknowledged": {"investigating"}, "investigating": {"response_sent"},
                           "response_sent": {"closed", "escalated_afca"}},
                  "categories": ["service", "claims", "premium_or_fees", "advice", "policy_terms", "privacy", "other"],
                  "outcomes": ["resolved_in_client_favour", "partially_resolved", "not_upheld", "withdrawn", "escalated_afca"]},
    "privacy": {"coll": "privacy_requests", "event": "PrivacyRequestStageChanged", "label": "Privacy request",
                "stages": ["received", "in_progress", "completed", "refused"],
                "next": {"received": {"in_progress", "completed", "refused"}, "in_progress": {"completed", "refused"}},
                "categories": ["access", "correction", "complaint"], "outcomes": ["granted", "corrected", "refused", "resolved"]},
}
LABEL = {"received": "Received", "acknowledged": "Acknowledged", "investigating": "Investigating", "response_sent": "Response sent",
         "closed": "Closed", "escalated_afca": "Escalated to AFCA", "in_progress": "In progress", "completed": "Completed",
         "refused": "Refused"}
TERMINAL = {"closed", "escalated_afca", "completed", "refused"}
DUE_DAYS = 30
MARKS = (5, 2)


class Bad(ValueError):
    pass


def _utc(dt):
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    return dt.replace(tzinfo=timezone.utc) if dt and dt.tzinfo is None else dt


def clock(received: datetime, state: str | None) -> dict:
    received = _utc(received)
    return {"ack_due": holidays.next_business_dt(received, state), "response_due": received + timedelta(days=DUE_DAYS)}


def _sha(*parts) -> str:
    return hashlib.sha256("\x1f".join(str(p or "") for p in parts).encode()).hexdigest()


async def _event(store, tid, kind, m, frm, to, by, role, idx, extra=None):
    try:
        await append_via_dispatch(store, tid, KIND[kind]["event"], actor={"kind": "human" if by != "system" else "system", "id": by,
                                  "name": role}, payload={"matter_id": m["matter_id"], "kind": kind, "client_id": m["client_id"],
                                  "category": m["category"], "from": frm, "to": to, "content_sha256": _sha(m.get("summary"), m.get("details")),
                                  **(extra or {})}, idempotency_key=f"{kind}:{m['matter_id']}:{idx}:{to}")
    except DuplicateIdempotencyKey:
        pass


async def create(store, tid, kind, *, client_id, category, summary, details, source, source_ref=None, by, role,
                 received_at=None, assigned_user_id=None) -> dict:
    spec = KIND[kind]
    if category not in spec["categories"]:
        raise Bad(f"Category must be one of {', '.join(spec['categories'])}.")
    if not (summary or "").strip():
        raise Bad("Say what it's about.")
    received = _utc(received_at) or datetime.now(timezone.utc)
    safe = wrap(store)
    async with use_tenant(tid):
        c = await safe.clients.find_one({"client_id": client_id})
        if not c:
            raise Bad("Unknown client.")
        assigned = assigned_user_id or c.get("assigned_ae_user_id")
    st = await holidays.state_for(store, tid)
    m = {"matter_id": str(uuid.uuid4()), "kind": kind, "client_id": client_id, "client_name": c.get("display_name"),
         "category": category, "summary": summary.strip()[:300], "details": (details or "").strip()[:5000], "source": source,
         "source_ref": source_ref, "received_at": received, **clock(received, st), "stage": "received", "stage_since": received,
         "history": [{"to": "received", "at": received, "by": by, "note": None}], "assigned_user_id": assigned,
         "raised_by": by, "raised_role": role, "outcome": None, "response_text": None, "alerts": [], "closed_at": None,
         "test": bool(c.get("test"))}
    async with use_tenant(tid):
        await getattr(safe, spec["coll"]).insert_one(dict(m))
    await _event(store, tid, kind, m, None, "received", by, role, 0, {"source": source,
                 "ack_due": m["ack_due"].isoformat(), "response_due": m["response_due"].isoformat()})
    for who in ("principal", "ae"):
        await _alert(store, tid, m, who, f"New {spec['label'].lower()}: {m['client_name']}",
                     f"{m['summary']} — acknowledge by {_fmt(m['ack_due'])}.", f"{kind}_new:{m['matter_id']}")
    return m


def _fmt(dt) -> str:
    return _utc(dt).astimezone(holidays.SYD).strftime("%a %d/%m/%Y %I:%M %p").replace(" 0", " ")


async def get(store, tid, kind, matter_id) -> dict:
    async with use_tenant(tid):
        m = await getattr(wrap(store), KIND[kind]["coll"]).find_one({"matter_id": matter_id}, {"_id": 0})
    if not m:
        raise LookupError("not_found")
    return m


async def move(store, tid, kind, m, to, *, by, role, note=None, outcome=None, response_text=None, afca_text=None) -> dict:
    spec = KIND[kind]
    if to not in spec["next"].get(m["stage"], set()):
        raise Bad(f"Can't move from {LABEL[m['stage']]} to {LABEL[to]}.")
    sets = {"stage": to, "stage_since": datetime.now(timezone.utc)}
    if kind == "complaint" and to == "response_sent":
        text = (response_text or "").strip()
        if not text or not outcome:
            raise Bad("The final response needs the response text and the outcome.")
        if afca_text and afca_text not in text:
            text = f"{text}\n\n{afca_text}"  # the IDR response must carry the AFCA details
        sets.update(response_text=text, outcome=outcome, response_sent_at=sets["stage_since"])
    if to in TERMINAL:
        if outcome:
            sets["outcome"] = outcome
        if kind == "complaint" and to == "escalated_afca":
            sets["outcome"] = "escalated_afca"
        if not sets.get("outcome") and not m.get("outcome"):
            raise Bad("Record the outcome.")
        sets["closed_at"] = sets["stage_since"]
    idx = len(m.get("history") or [])
    h = {"to": to, "at": sets["stage_since"], "by": by, "note": (note or "")[:500] or None}
    async with use_tenant(tid):
        res = await getattr(wrap(store), spec["coll"]).update_one({"matter_id": m["matter_id"], "stage": m["stage"]},
                                                                  {"$set": sets, "$push": {"history": h}})
    if not res.modified_count:
        raise Bad("This changed in the meantime. Refresh and try again.")
    await _event(store, tid, kind, m, m["stage"], to, by, role, idx,
                 {"outcome": sets.get("outcome"), "response_sha256": _sha(sets.get("response_text")) if sets.get("response_text") else None})
    if kind == "complaint" and to in ("acknowledged", "response_sent"):
        await _alert(store, tid, m, "client", "Your complaint: " + LABEL[to].lower(),
                     "We've received your complaint and are looking into it." if to == "acknowledged" else "Our response to your complaint is ready.",
                     f"complaint_client:{m['matter_id']}:{to}")
    m.update(sets)
    m["history"] = (m.get("history") or []) + [h]
    return m


async def _alert(store, tid, m, who, title, body, idem):
    safe = wrap(store)
    async with use_tenant(tid):
        if who == "principal":
            to = await notify._staff(safe, ("principal",))
        elif who == "ae":
            to = [m["assigned_user_id"]] if m.get("assigned_user_id") else []
        else:
            to = await notify._members(safe, m["client_id"], None)
    link = (f"/o/compliance?{m['kind']}={m['matter_id']}" if who != "client"
            else ("/c/complaint" if m["kind"] == "complaint" else "/c/my-data"))
    for uid in dict.fromkeys(to):
        await notify.deliver(store, tid, uid, kind=f"{m['kind']}_clock", title=title, many=title, body=body, link=link,
                             dedupe_key=f"{m['kind']}:{m['matter_id']}", idem=f"{idem}:{uid}", test=bool(m.get("test")))


async def tick(store, tid, now=None) -> int:
    """Due-date alerts: 5 and 2 days before the response is due, and once overdue (plus a missed acknowledgement)."""
    now, n = _utc(now) or datetime.now(timezone.utc), 0
    for kind, spec in KIND.items():
        async with use_tenant(tid):
            rows = [m async for m in getattr(wrap(store), spec["coll"]).find({"stage": {"$nin": list(TERMINAL)}}, {"_id": 0})]
        for m in rows:
            due, sent = _utc(m["response_due"]), set(m.get("alerts") or [])
            marks = [f"due{d}" for d in MARKS if now >= due - timedelta(days=d)] + (["overdue"] if now >= due else [])
            if kind == "complaint" and m["stage"] == "received" and now >= _utc(m["ack_due"]):
                marks.append("ack_overdue")
            for mark in [x for x in marks if x not in sent][-1:]:
                title = {"overdue": f"{spec['label']} OVERDUE", "ack_overdue": "Complaint not acknowledged in 1 business day"}.get(
                    mark, f"{spec['label']} due in {mark[3:]} days")
                for who in ("ae", "principal"):
                    await _alert(store, tid, m, who, f"{title}: {m['client_name']}", f"Response due {_fmt(due)}.",
                                 f"{kind}_{mark}:{m['matter_id']}")
                async with use_tenant(tid):
                    await getattr(wrap(store), spec["coll"]).update_one({"matter_id": m["matter_id"]},
                                                                        {"$addToSet": {"alerts": {"$each": marks}}})
                n += 1
    return n


def visible(m: dict, s) -> bool:
    return s.active_role == "principal" or m.get("assigned_user_id") == s.user_id or m.get("raised_by") == s.user_id


async def register(store, tid, kind, s=None, client_ids=None) -> list[dict]:
    q = {"client_id": {"$in": list(client_ids)}} if client_ids is not None else {}
    async with use_tenant(tid):
        rows = [m async for m in getattr(wrap(store), KIND[kind]["coll"]).find(q, {"_id": 0}).sort("received_at", -1)]
    return [m for m in rows if s is None or visible(m, s)]


def idr_stats(rows: list[dict], now=None) -> dict:
    now = _utc(now) or datetime.now(timezone.utc)
    closed = [m for m in rows if m.get("closed_at") or m.get("response_sent_at")]
    days = [(_utc(m.get("response_sent_at") or m["closed_at"]) - _utc(m["received_at"])).days for m in closed]
    count = lambda key: {k: sum(1 for m in rows if m.get(key) == k) for k in {m.get(key) for m in rows} if k}  # noqa: E731
    return {"total": len(rows), "open": sum(1 for m in rows if m["stage"] not in TERMINAL),
            "overdue": sum(1 for m in rows if m["stage"] not in TERMINAL and now > _utc(m["response_due"])),
            "by_category": count("category"), "by_outcome": count("outcome"), "by_stage": count("stage"),
            "avg_days_to_resolve": round(sum(days) / len(days), 1) if days else None,
            "resolved_within_30_days": sum(1 for d in days if d <= DUE_DAYS), "data_basis": "register"}
