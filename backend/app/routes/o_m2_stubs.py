"""R0.7 — M2 carry stubs: Weekly pack (Principal/Operator), Renewals + Inbox (any staff role)."""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.auth.sessions import Session
from app.db.tenant_safe import wrap
from app.kernel import spend
from app.kernel.tenant_ctx import use_tenant
from app.middleware.session import require_operator_session

router = APIRouter(prefix="/api/o")
ASK_TYPES = ["ClientQuestionAsked", "ClientMessageReleased", "ClientMessageHITL", "ClientMessageRewriteRequired"]
THREAD_STATUS = {"ClientQuestionAsked": "Awaiting broker reply", "ClientMessageReleased": "Answered",
                 "ClientMessageHITL": "Reply with broker for review", "ClientMessageRewriteRequired": "Reply being rewritten"}


def require_governing(session: Session = Depends(require_operator_session)) -> Session:
    if session.active_role not in ("principal", "operator"):
        raise HTTPException(status_code=403, detail="principal_or_operator_required")
    return session


def _utc(dt):
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    return dt.replace(tzinfo=timezone.utc) if dt and dt.tzinfo is None else dt


def _ddmmyyyy(dt) -> str:
    return _utc(dt).astimezone(timezone(timedelta(hours=10))).strftime("%d/%m/%Y")


def _n(v, basis="registry"):
    return {"value": v, "data_basis": basis}


async def _weekly(request: Request, tenant_id: str) -> dict:
    safe, now = wrap(request.app.state.mongo_db), datetime.now(timezone.utc)
    start = now - timedelta(days=7)
    async with use_tenant(tenant_id):
        open_rows = [r async for r in safe.proj_queue.find({"resolved_at": None})]
        done = [r async for r in safe.proj_queue.find({"resolved_at": {"$gte": start}})]
        approvals = await safe.events.count_documents(
            {"type": {"$in": ["Approval", "OperatorApproval"]}, "ts": {"$gte": start}})
    overdue = sum(1 for r in open_rows if r.get("sla_deadline") and _utc(r["sla_deadline"]) < now)
    kept = sum(1 for r in done if not r.get("sla_deadline") or _utc(r["resolved_at"]) <= _utc(r["sla_deadline"]))
    s = await spend.summary(request.app.state.mongo_db, tenant_id)
    return {
        "week_from": _ddmmyyyy(start), "week_to": _ddmmyyyy(now),
        "metrics": [
            {"key": "promises_kept", "label": "Promises kept this week", **_n(kept)},
            {"key": "promises_open", "label": "Open promises", **_n(len(open_rows) - overdue)},
            {"key": "promises_overdue", "label": "Overdue promises", **_n(overdue)},
            {"key": "approvals", "label": "Approvals this week", **_n(approvals)},
            {"key": "spend", "label": "Spend ESTIMATE (sprint to date, USD)", **_n(round(s["cost_usd"], 2), "estimate")},
        ],
        "note": "Promises are counted from queue reply-by times until the promises engine lands.",
    }


@router.get("/weekly-pack")
async def weekly_pack(request: Request, session: Session = Depends(require_governing)) -> dict:
    return await _weekly(request, session.tenant_id)


@router.get("/weekly-pack.pdf")
async def weekly_pack_pdf(request: Request, session: Session = Depends(require_governing)) -> Response:
    w = await _weekly(request, session.tenant_id)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = A4[1] - 72
    c.setFont("Helvetica-Bold", 16)
    c.drawString(56, y, "Harbourline Insurance Brokers — Weekly pack")
    c.setFont("Helvetica", 10)
    c.drawString(56, y - 18, f"Week {w['week_from']} to {w['week_to']}")
    y -= 56
    for m in w["metrics"]:
        c.setFont("Helvetica", 12)
        c.drawString(56, y, m["label"])
        c.drawRightString(A4[0] - 140, y, str(m["value"]))
        c.setFont("Helvetica", 8)
        c.drawString(A4[0] - 130, y, f"basis: {m['data_basis']}")
        y -= 24
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(56, y - 12, w["note"])
    c.showPage()
    c.save()
    return Response(buf.getvalue(), media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="harbourline-weekly-pack.pdf"'})


def _renewal_step(days: int) -> str:
    if days < 0:
        return "Confirm lapse or late renewal"
    if days <= 30:
        return "Send renewal terms"
    if days <= 60:
        return "Request insurer renewal terms"
    if days <= 90:
        return "Check client details"
    return "No action yet"


@router.get("/renewals")
async def renewals(request: Request, session: Session = Depends(require_operator_session)) -> dict:
    safe, now = wrap(request.app.state.mongo_db), datetime.now(timezone.utc)
    async with use_tenant(session.tenant_id):
        names = {c["client_id"]: c["display_name"] async for c in safe.clients.find({})}
        pols = [p async for p in safe.policies.find({"status": "managed", "expiry": {"$ne": None}})]
    rows = []
    for p in sorted(pols, key=lambda p: _utc(p["expiry"])):
        exp = _utc(p["expiry"])
        days = (exp - now).days
        rows.append({
            "policy_id": p["policy_id"], "client": names.get(p["client_id"], "Unknown client"),
            "policy": p.get("product"), "insurer": p.get("insurer_display"),
            "expiry": _ddmmyyyy(exp), "days_to_go": _n(max(days, 0)), "expired": days < 0,
            "promise": f"Renewal terms to client by {_ddmmyyyy(exp - timedelta(days=30))}",
            "next_step": _renewal_step(days),
        })
    return {"renewals": rows, "count": _n(len(rows))}


@router.get("/inbox")
async def inbox(request: Request, session: Session = Depends(require_operator_session)) -> dict:
    safe = wrap(request.app.state.mongo_db)
    threads: dict[tuple, dict] = {}
    async with use_tenant(session.tenant_id):
        names = {c["client_id"]: c["display_name"] async for c in safe.clients.find({})}
        async for e in safe.events.find({"type": {"$in": ASK_TYPES}}).sort("seq", 1):
            p = e.get("payload") or {}
            key = (p.get("client_id"), p.get("matter_id") or p.get("envelope_id"))
            t = threads.setdefault(key, {"thread_id": key[1], "client": names.get(key[0], "Unknown client"),
                                         "subject": p.get("message"), "messages": _n(0)})
            t["messages"]["value"] += 1
            t["last_type"], t["last_ts"] = e["type"], _utc(e["ts"])
            if p.get("message"):
                t["last_message"] = p["message"]
    rows = []
    for t in sorted(threads.values(), key=lambda t: t["last_ts"], reverse=True):
        waiting = t["last_type"] != "ClientMessageReleased"
        rows.append({**{k: v for k, v in t.items() if k not in ("last_type", "last_ts")},
                     "last_at": t["last_ts"].isoformat(), "status": THREAD_STATUS[t["last_type"]],
                     "promise": f"Reply by {_ddmmyyyy(t['last_ts'] + timedelta(days=1))}" if waiting else "Kept"})
    return {"threads": rows, "count": _n(len(rows))}
