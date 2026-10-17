"""R1.7 — records retention (report only, never auto-delete) and Principal-approved PII redaction of the projection.

The ledger is append-only and is never edited. Redaction replaces PII on the client projection (and contact
fields) with a placeholder, after a Principal approves it, and writes a RecordRedacted event listing the fields.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.db.tenant_safe import wrap
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch

DEFAULT_YEARS = {"client": 7, "policy": 7, "complaint": 7, "privacy_request": 7}
PII_FIELDS = ("display_name", "legal_name", "email", "phone", "abn", "address", "contacts", "trading_name")


def _utc(dt):
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    return dt.replace(tzinfo=timezone.utc) if dt and dt.tzinfo is None else dt


def _plus_years(d: datetime, n: int) -> datetime:
    try:
        return d.replace(year=d.year + n)
    except ValueError:
        return d.replace(year=d.year + n, day=28)


async def policy_years(store, tid) -> dict:
    async with use_tenant(tid):
        row = await wrap(store).tenant_settings.find_one({}, {"_id": 0}) or {}
    return {**DEFAULT_YEARS, **(row.get("retention_years") or {})}


async def report(store, tid, now=None) -> dict:
    now = _utc(now) or datetime.now(timezone.utc)
    years, safe, rows = await policy_years(store, tid), wrap(store), []
    async with use_tenant(tid):
        clients = {c["client_id"]: c async for c in safe.clients.find({}, {"_id": 0})}
        pols = [p async for p in safe.policies.find({}, {"_id": 0, "policy_id": 1, "client_id": 1, "expiry": 1, "status": 1, "product": 1})]
        matters = [("complaint", m) async for m in safe.complaints.find({"closed_at": {"$ne": None}}, {"_id": 0})]
        matters += [("privacy_request", m) async for m in safe.privacy_requests.find({"closed_at": {"$ne": None}}, {"_id": 0})]
    last = {}
    for p in pols:
        if p.get("expiry"):
            last[p["client_id"]] = max(last.get(p["client_id"], _utc(p["expiry"])), _utc(p["expiry"]))
        if p.get("expiry") and p.get("status") not in ("managed",):
            ends = _plus_years(_utc(p["expiry"]), years["policy"])
            rows.append({"record_type": "policy", "id": p["policy_id"], "client_id": p["client_id"], "label": p.get("product"),
                         "ended": _utc(p["expiry"]), "retain_until": ends})
    active = {p["client_id"] for p in pols if p.get("status") == "managed"}
    for cid, c in clients.items():
        if cid in active or c.get("redacted_at"):
            continue
        end = last.get(cid) or _utc(c.get("created_at"))
        if end:
            rows.append({"record_type": "client", "id": cid, "client_id": cid, "label": c.get("display_name"), "ended": end,
                         "retain_until": _plus_years(end, years["client"])})
    for kind, m in matters:
        rows.append({"record_type": kind, "id": m["matter_id"], "client_id": m["client_id"], "label": m.get("category"),
                     "ended": _utc(m["closed_at"]), "retain_until": _plus_years(_utc(m["closed_at"]), years[kind])})
    for r in rows:
        c = clients.get(r["client_id"]) or {}
        r["legal_hold"] = bool(c.get("legal_hold"))
        r["past_retention"] = r["retain_until"] <= now
        r["redactable"] = r["record_type"] == "client" and r["past_retention"] and not r["legal_hold"]
    past = [r for r in rows if r["past_retention"]]
    return {"years": years, "past_retention": past, "on_hold": sum(1 for r in rows if r["legal_hold"]), "checked": len(rows),
            "auto_delete": False, "data_basis": "registry"}


async def set_hold(store, tid, client_id, hold: bool, reason: str, by: str) -> None:
    async with use_tenant(tid):
        await wrap(store).clients.update_one({"client_id": client_id}, {"$set": {"legal_hold": hold, "legal_hold_reason": reason}})
    await append_via_dispatch(store, tid, "LegalHoldSet", actor={"kind": "human", "id": by, "name": "principal"},
                              payload={"client_id": client_id, "hold": hold, "reason": reason},
                              idempotency_key=f"legal_hold:{client_id}:{datetime.now(timezone.utc).isoformat()}")


async def redact(store, tid, client_id, note: str, by: str, now=None) -> dict:
    rep = await report(store, tid, now)
    hit = next((r for r in rep["past_retention"] if r["record_type"] == "client" and r["id"] == client_id), None)
    if not hit or not hit["redactable"]:
        raise ValueError("Only a client past retention and not on legal hold can be redacted.")
    if not (note or "").strip():
        raise ValueError("Record why this is approved.")
    async with use_tenant(tid):
        c = await wrap(store).clients.find_one({"client_id": client_id}) or {}
        fields = [f for f in PII_FIELDS if c.get(f) not in (None, "", [], {})]
        sets = {f: None for f in fields} | {"display_name": f"Redacted client {client_id[:6]}", "redacted_at": datetime.now(timezone.utc),
                                            "redacted_by": by}
        await wrap(store).clients.update_one({"client_id": client_id}, {"$set": sets})
    await append_via_dispatch(store, tid, "RecordRedacted", actor={"kind": "human", "id": by, "name": "principal"},
                              payload={"record_type": "client", "client_id": client_id, "fields": fields, "approved_by": by,
                                       "note": note[:300], "ledger_untouched": True}, idempotency_key=f"redact:{client_id}")
    return {"client_id": client_id, "fields": fields}
