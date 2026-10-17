"""R0.7 — M2 carry stubs: Weekly pack (Principal/Operator), Renewals + Inbox (any staff role)."""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.auth.sessions import Session
from app.db.tenant_safe import wrap
from app.kernel import inbox as inbox_k, spend
from app.kernel.tenant_ctx import use_tenant
from app.middleware.session import require_operator_session
from app.kernel.test_data import test_client_ids, test_event_filter

router = APIRouter(prefix="/api/o")
PROMISE_BASIS = "queue + inbox reply-by"


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
        tids = await test_client_ids(safe)
        open_rows = [r async for r in safe.proj_queue.find({"resolved_at": None, "client_id": {"$nin": tids}})]
        done = [r async for r in safe.proj_queue.find({"resolved_at": {"$gte": start}, "client_id": {"$nin": tids}})]
        approvals = await safe.events.count_documents(
            {**(await test_event_filter(safe)), "type": {"$in": ["Approval", "OperatorApproval"]}, "ts": {"$gte": start}})
    overdue = sum(1 for r in open_rows if r.get("sla_deadline") and _utc(r["sla_deadline"]) < now)
    kept = sum(1 for r in done if not r.get("sla_deadline") or _utc(r["resolved_at"]) <= _utc(r["sla_deadline"]))
    open_q = len(open_rows) - overdue
    threads = [t for t in await inbox_k.load_threads(request.app.state.mongo_db, tenant_id) if t["reply_by"]]
    t_status = [inbox_k.status_for(t, now) for t in threads]
    overdue += t_status.count("Overdue")
    open_q += t_status.count("Awaiting reply") + t_status.count("Due today")
    kept += sum(1 for t in threads if t["kept"] and not t["kept"]["late"] and t["kept"]["kept_at"] >= start)
    s = await spend.summary(request.app.state.mongo_db, tenant_id)
    return {
        "week_from": _ddmmyyyy(start), "week_to": _ddmmyyyy(now),
        "metrics": [
            {"key": "promises_kept", "label": "Promises kept this week", **_n(kept, PROMISE_BASIS)},
            {"key": "promises_open", "label": "Open promises", **_n(open_q, PROMISE_BASIS)},
            {"key": "promises_overdue", "label": "Overdue promises", **_n(overdue, PROMISE_BASIS)},
            {"key": "approvals", "label": "Approvals this week", **_n(approvals)},
            {"key": "spend", "label": "Spend ESTIMATE (sprint to date, USD)", **_n(round(s["cost_usd"], 2), "estimate")},
        ],
        "note": "Promises are counted from queue reply-by times and client-thread reply-by times (+1 business day). Kept means answered on time. The full promises engine comes later.",
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
        pols = [p async for p in safe.policies.find({"status": "managed", "expiry": {"$ne": None},
                                                      "client_id": {"$nin": await test_client_ids(safe)}})]
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


def _thread_row(t: dict, now: datetime) -> dict:
    return {"thread_id": t["thread_id"], "client": t["client"], "subject": t["subject"],
            "last_message": t["last_message"], "last_at": t["last_at"].isoformat(),
            "reply_by": t["reply_by"].isoformat() if t["reply_by"] else None,
            "reply_by_text": inbox_k.fmt_short(t["reply_by"]) if t["reply_by"] else "No reply-by",
            "reply_by_basis": inbox_k.BASIS, "status": inbox_k.status_for(t, now),
            "messages": _n(len([m for m in t["messages"] if m["who"] != "pending"]))}


@router.get("/inbox")
async def inbox(request: Request, include_tests: bool = False,
                session: Session = Depends(require_operator_session)) -> dict:
    now = datetime.now(timezone.utc)
    rows = await inbox_k.load_threads(request.app.state.mongo_db, session.tenant_id, include_tests=include_tests)
    out = [_thread_row(t, now) for t in inbox_k.sort_threads(rows, now)]
    return {"threads": out, "count": _n(len(out))}


@router.get("/inbox/{thread_id}")
async def inbox_thread(thread_id: str, request: Request,
                       session: Session = Depends(require_operator_session)) -> dict:
    now = datetime.now(timezone.utc)
    t = await inbox_k.get_thread(request.app.state.mongo_db, session.tenant_id, thread_id)
    msgs = [{"who": m["who"], "text": m["text"], "at": m["at"].isoformat(), "author": m.get("author")}
            for m in t["messages"]]
    return {**_thread_row(t, now), "conversation": msgs,
            "can_reply": session.active_role in inbox_k.REPLY_ROLES,
            "kept": ({"kept_at": t["kept"]["kept_at"].isoformat(), "late": t["kept"]["late"]} if t["kept"] else None)}


class ReplyBody(BaseModel):
    body: str = Field(max_length=inbox_k.MAX_BODY)
    idempotency_key: str = Field(min_length=8, max_length=200)

    @field_validator("body")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("reply cannot be empty")
        return v.strip()


@router.post("/inbox/{thread_id}/reply")
async def inbox_reply(thread_id: str, body: ReplyBody, request: Request,
                      session: Session = Depends(require_operator_session)) -> dict:
    return await inbox_k.reply(request.app.state.mongo_db, session, thread_id, body.body, body.idempotency_key)
