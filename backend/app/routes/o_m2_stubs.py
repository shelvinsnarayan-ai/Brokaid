"""R0.7 — M2 carry stubs: Weekly pack (Principal/Operator), Renewals + Inbox (any staff role)."""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator, model_validator
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


async def _renewal_metrics(safe, start) -> list[dict]:
    """R1.6: weekly-pack renewal numbers from real pipeline stages."""
    rows = [r async for r in safe.renewals.find({"test": {"$ne": True}}, {"stage": 1, "lapse_alerted": 1, "history": 1})]
    open_ = [r for r in rows if r["stage"] not in ("bound", "lapsed", "declined")]

    def week(to):
        return sum(1 for r in rows if any(h["to"] == to and _utc(h["at"]) >= start for h in r.get("history") or []))
    basis = "renewal pipeline"
    return [{"key": "renewals_open", "label": "Renewals in progress", **_n(len(open_), basis)},
            {"key": "renewals_lapse_risk", "label": "Renewals at lapse risk", **_n(sum(1 for r in open_ if r.get("lapse_alerted")), basis)},
            {"key": "renewals_bound", "label": "Renewals bound this week", **_n(week("bound"), basis)},
            {"key": "renewals_lapsed", "label": "Renewals lapsed this week", **_n(week("lapsed"), basis)}]


async def _weekly(request: Request, tenant_id: str) -> dict:
    safe, now = wrap(request.app.state.mongo_db), datetime.now(timezone.utc)
    start = now - timedelta(days=7)
    async with use_tenant(tenant_id):
        renewal_rows = await _renewal_metrics(safe, start)
        from app.kernel import claims as claims_k
        claim_rows = await claims_k.weekly_metrics(safe, now)
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
            *renewal_rows,
            *claim_rows,
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


def _thread_row(t: dict, now: datetime, claims: dict | None = None) -> dict:
    return {"thread_id": t["thread_id"], "client": t["client"], "subject": t["subject"],
            "last_message": t["last_message"], "last_at": t["last_at"].isoformat(),
            "reply_by": t["reply_by"].isoformat() if t["reply_by"] else None,
            "reply_by_text": inbox_k.fmt_short(t["reply_by"]) if t["reply_by"] else "No reply-by",
            "reply_by_basis": inbox_k.BASIS, "status": inbox_k.status_for(t, now),
            "messages": _n(len([m for m in t["messages"] if m["who"] != "pending"])),
            "claim": {"claim_id": t["claim_id"], "label": (claims or {}).get(t["claim_id"]) or "Claim"} if t.get("claim_id") else None}


async def _claim_labels(store, tenant_id: str) -> dict:
    from app.db.tenant_safe import wrap
    from app.kernel import claim_threads
    from app.kernel.tenant_ctx import use_tenant
    async with use_tenant(tenant_id):
        return {c["claim_id"]: f"{c.get('claim_reference')} · {claim_threads.label(c)}"
                async for c in wrap(store).claims.find({}, {"_id": 0, "claim_id": 1, "claim_reference": 1, "claim_class": 1,
                                                            "claim_type": 1, "incident": 1, "product": 1})}


@router.get("/inbox")
async def inbox(request: Request, include_tests: bool = False,
                session: Session = Depends(require_operator_session)) -> dict:
    now = datetime.now(timezone.utc)
    rows = await inbox_k.load_threads(request.app.state.mongo_db, session.tenant_id, include_tests=include_tests)
    labels = await _claim_labels(request.app.state.mongo_db, session.tenant_id)
    out = [_thread_row(t, now, labels) for t in inbox_k.sort_threads(rows, now)]
    return {"threads": out, "count": _n(len(out))}


@router.get("/inbox/{thread_id}")
async def inbox_thread(thread_id: str, request: Request,
                       session: Session = Depends(require_operator_session)) -> dict:
    now = datetime.now(timezone.utc)
    t = await inbox_k.get_thread(request.app.state.mongo_db, session.tenant_id, thread_id)
    claim = None
    if str(thread_id).startswith("claim-"):  # R3.5: attachments resolve against the claim's files
        from app.db.tenant_safe import wrap as _w
        from app.kernel.tenant_ctx import use_tenant as _ut
        async with _ut(session.tenant_id):
            claim = await _w(request.app.state.mongo_db).claims.find_one({"claim_id": thread_id[6:]}, {"_id": 0, "docs": 1, "claim_id": 1, "stage": 1, "status": 1})
    from app.kernel import claim_threads as _ct
    msgs = [{"who": m["who"], "text": m["text"], "at": m["at"].isoformat(), "author": m.get("author"),
             "attachments": _ct.attachments(claim, m.get("file_ids") or []) if claim else [],
             "claim_id": claim["claim_id"] if claim else None}
            for m in t["messages"]]
    return {**_thread_row(t, now, await _claim_labels(request.app.state.mongo_db, session.tenant_id)), "conversation": msgs,
            "can_reply": session.active_role in inbox_k.REPLY_ROLES, "me": {"user_id": session.user_id, "role": session.active_role},
            "attachment_limits": ({"max_files": 5, "photo_bytes": 10 * 1024 * 1024, "pdf_bytes": 20 * 1024 * 1024,
                                   "message_bytes": 250 * 1024 * 1024, "videos_enabled": False, "documents_enabled": True,
                                   "closed": _ct.is_closed(claim)} if claim else None),
            "advice_approvals": await inbox_k.approvals(request.app.state.mongo_db, session.tenant_id, thread_id),
            "kept": ({"kept_at": t["kept"]["kept_at"].isoformat(), "late": t["kept"]["late"]} if t["kept"] else None)}


class ReplyBody(BaseModel):
    body: str = Field(default="", max_length=inbox_k.MAX_BODY)
    idempotency_key: str = Field(min_length=8, max_length=200)
    approval_id: str | None = None
    file_ids: list[str] = Field(default_factory=list, max_length=5)  # R3.6: claim threads only
    draft_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    labels: dict[str, str] = Field(default_factory=dict, max_length=5)
    quotes: dict[str, dict] = Field(default_factory=dict, max_length=5)  # R3.8: repair-quote summary per file

    @field_validator("body")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @model_validator(mode="after")
    def _not_blank(self):
        if not self.body and not self.file_ids:
            raise ValueError("reply cannot be empty")
        if self.file_ids and not self.draft_id:
            raise ValueError("draft_id is required with attachments")
        return self


@router.post("/inbox/{thread_id}/reply")
async def inbox_reply(thread_id: str, body: ReplyBody, request: Request,
                      session: Session = Depends(require_operator_session)) -> dict:
    store = request.app.state.mongo_db
    if not body.file_ids:
        return await inbox_k.reply(store, session, thread_id, body.body, body.idempotency_key, body.approval_id)
    # R3.6: attachments ride on a normal reply (advice guard, GAW, PromiseKept, audit unchanged), then become claim files
    from app.db.tenant_safe import wrap as _w
    from app.kernel import claim_threads as _ct, claims as _ck, media as _media
    from app.kernel.tenant_ctx import use_tenant as _ut
    if not str(thread_id).startswith(_ct.PREFIX):
        raise HTTPException(status_code=400, detail={"code": "attachments_claim_only", "detail": "Files can only be attached on claim threads."})
    if session.active_role not in inbox_k.REPLY_ROLES:
        raise HTTPException(status_code=403, detail={"code": "AuthorityDenied", "detail": "your role can read threads but not reply"})
    async with _ut(session.tenant_id):
        c = await _w(store).claims.find_one({"claim_id": thread_id[len(_ct.PREFIX):]}, {"_id": 0})
    if not c:
        raise HTTPException(status_code=404, detail="thread_not_found")
    if _ct.is_closed(c):
        raise HTTPException(status_code=409, detail={"code": "claim_closed", "detail": "This claim is closed."})
    staged = {r["file_id"]: r["doc"] for r in await _ct.staged(store, session.tenant_id, c["claim_id"], body.draft_id, session.user_id)
              if not r.get("held_by")}  # R3.7: files held by a pending approval only go out through it
    async with _ut(session.tenant_id):
        prior = await _w(store).thread_messages.find_one({"idempotency_key": body.idempotency_key})
    if not prior and (len(set(body.file_ids)) != len(body.file_ids) or any(f not in staged for f in body.file_ids)):
        raise HTTPException(status_code=400, detail={"code": "attachment_invalid",
                                                     "detail": "One of the attachments is missing or was removed. Remove it and attach it again."})
    docs = [{**staged[f], "label": body.labels.get(f) if body.labels.get(f) in _media.LABELS else staged[f].get("label")}
            for f in body.file_ids if f in staged]
    quotes = _quotes(body.file_ids, body.labels, body.quotes, staged) if not prior else {}
    r = await inbox_k.reply(store, session, thread_id, body.body, body.idempotency_key, body.approval_id,
                            file_ids=body.file_ids, attachment_summary=_ct.summary(docs, broker=True))
    if not r["replayed"]:
        try:
            await _ct.attach(store, session.tenant_id, c, file_ids=body.file_ids, msg_id=body.draft_id, user_id=session.user_id,
                             role=session.active_role, reply_id=r["reply_id"], labels=body.labels, quotes=quotes)
        except _ck.Bad as e:
            raise HTTPException(status_code=400, detail={"code": "attachment_invalid", "detail": str(e)}) from e
    return r


class ApprovalBody(BaseModel):
    body: str = Field(min_length=1, max_length=inbox_k.MAX_BODY)
    file_ids: list[str] = Field(default_factory=list, max_length=5)  # R3.7: files held with the approval (claim threads)
    draft_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    labels: dict[str, str] = Field(default_factory=dict, max_length=5)
    quotes: dict[str, dict] = Field(default_factory=dict, max_length=5)


def _quotes(file_ids: list[str], labels: dict, quotes: dict, staged: dict) -> dict:
    """R3.8: validate repair-quote summaries for files labelled repair_quote (blank → no card)."""
    from app.kernel import repair_quote as _rq
    out = {}
    for f in file_ids:
        d = staged.get(f) or {}
        if (labels.get(f) or d.get("label")) == "repair_quote":
            try:
                q = _rq.clean(quotes.get(f), d.get("quote_suggested"))
            except ValueError as e:
                raise HTTPException(status_code=422, detail={"code": "quote_invalid", "detail": str(e)}) from e
            if q:
                out[f] = q
    return out


@router.post("/inbox/{thread_id}/advice-approval")
async def advice_approval(thread_id: str, body: ApprovalBody, request: Request,
                          session: Session = Depends(require_operator_session)) -> dict:
    store = request.app.state.mongo_db
    if not body.file_ids:
        return await inbox_k.request_advice_approval(store, session, thread_id, body.body.strip())
    from app.kernel import approval_files as _af, claim_threads as _ct
    if not str(thread_id).startswith(_ct.PREFIX) or not body.draft_id:
        raise HTTPException(status_code=400, detail={"code": "attachments_claim_only", "detail": "Files can only be attached on claim threads."})
    if session.active_role not in inbox_k.REPLY_ROLES:
        raise HTTPException(status_code=403, detail={"code": "AuthorityDenied", "detail": "your role can read threads but not reply"})
    c = await _af.claim_for(store, session.tenant_id, thread_id)
    if _ct.is_closed(c):
        raise HTTPException(status_code=409, detail={"code": "claim_closed", "detail": "This claim is closed."})
    staged = {r["file_id"]: r["doc"] for r in await _ct.staged(store, session.tenant_id, c["claim_id"], body.draft_id, session.user_id)}
    files = await _af.bind(store, session, c, draft_id=body.draft_id, file_ids=body.file_ids, labels=body.labels,
                           quotes=_quotes(body.file_ids, body.labels, body.quotes, staged))
    a = await inbox_k.request_advice_approval(store, session, thread_id, body.body.strip(), files=files, draft_id=body.draft_id)
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in a.items()}


@router.post("/inbox/{thread_id}/advice-approval/{approval_id}/approve")
async def advice_approve(thread_id: str, approval_id: str, request: Request,
                         session: Session = Depends(require_operator_session)) -> dict:
    """Text-only approvals: approve, then the author sends. With files (R3.7): approve sends the one reply with every file."""
    store = request.app.state.mongo_db
    a = await inbox_k.pending_approval(store, session, thread_id, approval_id)
    if a.get("files"):
        from app.kernel import approval_files as _af
        return await _af.approve_and_send(store, session, thread_id, a)
    return await inbox_k.approve_advice(store, session, thread_id, approval_id)


@router.post("/inbox/{thread_id}/advice-approval/{approval_id}/reject")
async def advice_reject(thread_id: str, approval_id: str, request: Request, session: Session = Depends(require_operator_session)) -> dict:
    return await inbox_k.decide_advice(request.app.state.mongo_db, session, thread_id, approval_id, "rejected")


@router.post("/inbox/{thread_id}/advice-approval/{approval_id}/withdraw")
async def advice_withdraw(thread_id: str, approval_id: str, request: Request, session: Session = Depends(require_operator_session)) -> dict:
    return await inbox_k.decide_advice(request.app.state.mongo_db, session, thread_id, approval_id, "withdrawn")


@router.get("/inbox/{thread_id}/advice-approval/{approval_id}/files/{file_id}")
async def advice_file(thread_id: str, approval_id: str, file_id: str, request: Request, preview: bool = False, download: bool = False,
                      session: Session = Depends(require_operator_session)):
    """Staff-only view of a file held with an approval (the Principal reviews before anything reaches the client)."""
    from app.db.tenant_safe import wrap as _w
    from app.kernel import approval_files as _af
    from app.kernel.tenant_ctx import use_tenant as _ut
    from app.routes.c_claims import _file_response
    async with _ut(session.tenant_id):
        a = await _w(request.app.state.mongo_db).reply_advice_approvals.find_one({"approval_id": approval_id, "thread_id": thread_id})
    d = a and await _af.staged_doc(request.app.state.mongo_db, session.tenant_id, a, file_id)
    if not d or (preview and not d.get("preview_path")):
        raise HTTPException(status_code=404, detail="file_not_found")
    return await _file_response(d, request, preview=preview, download=download)
