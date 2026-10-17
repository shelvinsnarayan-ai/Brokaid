"""R1.8 — /api/o/claims: AE claims list, lodge on behalf (phone), triage, info requests, AI summary, claim pack,
insurer submission (manual; MOCKED channel only in mocked_allowed tenants), assessing, decision, settlement, close/withdraw."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.auth.sessions import Session
from app.db.tenant_safe import wrap
from app.kernel import claims as ck
from app.kernel import holidays, object_store
from app.kernel.brain import BrainError
from app.kernel.governance import KillSwitchActive
from app.kernel.release import try_release
from app.kernel.tenant_ctx import use_tenant
from app.middleware.session import require_operator_session
from app.routes.c_claims import FNOLBody

router = APIRouter(prefix="/api/o/claims")
STAFF = ("ae", "principal", "operator")


def _store(request: Request):
    return request.app.state.mongo_db


def _staff(s: Session = Depends(require_operator_session)) -> Session:
    if s.active_role not in STAFF:
        raise HTTPException(status_code=403, detail="staff_only")
    return s


async def _run(coro):
    try:
        return await coro
    except (ck.Bad, ValueError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except LookupError:
        raise HTTPException(status_code=404, detail="claim_not_found") from None


async def _get(request, s, claim_id) -> dict:
    return await _run(ck.get(_store(request), s.tenant_id, claim_id))


def _view(c):
    return {"claim": ck.staff_view(c, datetime.now(timezone.utc))}


@router.get("")
async def list_claims(request: Request, stage: str | None = None, ae: str | None = None, insurer: str | None = None,
                      min_age: int | None = None, include_closed: bool = False, s: Session = Depends(_staff)) -> dict:
    now, safe = datetime.now(timezone.utc), wrap(_store(request))
    q: dict[str, Any] = {"stage": {"$exists": True} if include_closed else {"$exists": True, "$nin": list(ck.TERMINAL)}}
    if stage:
        q["stage"] = stage
    if ae:
        q["assigned_ae"] = s.user_id if ae == "me" else ae
    if insurer:
        q["insurer"] = insurer
    async with use_tenant(s.tenant_id):
        rows = [ck.row(c, now) async for c in safe.claims.find(q, {"_id": 0})]
        users = {u["user_id"]: u.get("display_name") async for u in safe.users.find({"role": {"$in": ["ae", "principal"]}})}
    rows = [r for r in rows if min_age is None or r["age_days"] >= min_age]
    rows.sort(key=lambda r: (not r["overdue"], r["lodged_at"]))
    for r in rows:
        r["ae_name"] = users.get(r["assigned_ae"])
    return {"claims": rows, "stages": [{"key": k, "label": ck.LABEL[k]} for k in ck.STAGES],
            "insurers": sorted({r["insurer"] for r in rows if r["insurer"]}), "aes": [{"user_id": k, "name": v} for k, v in users.items()],
            "quoting_mode": await holidays.quoting_mode(_store(request), s.tenant_id)}


class StaffFNOL(FNOLBody):
    client_id: str
    channel: str = "phone"


@router.post("")
async def lodge(body: StaffFNOL, request: Request, s: Session = Depends(_staff)) -> dict:
    store = _store(request)
    policy = None
    async with use_tenant(s.tenant_id):
        if not await wrap(store).clients.find_one({"client_id": body.client_id}):
            raise HTTPException(status_code=404, detail="client_not_found")
        if body.policy_id:
            policy = await wrap(store).policies.find_one({"policy_id": body.policy_id, "client_id": body.client_id}, {"_id": 0})
            if not policy:
                raise HTTPException(status_code=404, detail="policy_not_found")
    c = await _run(ck.lodge(store, s.tenant_id, client_id=body.client_id, policy=policy, body=body.model_dump(), by=s.user_id,
                            role=s.active_role, channel=body.channel if body.channel in ("phone", "email", "in_person") else "phone"))
    return _view(c)


@router.get("/{claim_id}")
async def detail(claim_id: str, request: Request, s: Session = Depends(_staff)) -> dict:
    c = await _get(request, s, claim_id)
    return {**_view(c), "quoting_mode": await holidays.quoting_mode(_store(request), s.tenant_id)}


@router.post("/{claim_id}/ack")
async def ack(claim_id: str, request: Request, s: Session = Depends(_staff)) -> dict:
    c = await _get(request, s, claim_id)
    return _view(await _run(ck.acknowledge(_store(request), s.tenant_id, c, by=s.user_id, role=s.active_role)))


@router.post("/{claim_id}/flags/refresh")
async def flags(claim_id: str, request: Request, s: Session = Depends(_staff)) -> dict:
    c = await _get(request, s, claim_id)
    return _view(await ck.refresh_flags(_store(request), s.tenant_id, c))


class InfoBody(BaseModel):
    items: list[str] = Field(min_length=1, max_length=10)
    due: str
    message: str = Field(default="", max_length=1000)


@router.post("/{claim_id}/info-request")
async def info(claim_id: str, body: InfoBody, request: Request, s: Session = Depends(_staff)) -> dict:
    c = await _get(request, s, claim_id)
    return _view(await _run(ck.request_info(_store(request), s.tenant_id, c, items=body.items, due=body.due, message=body.message,
                                            by=s.user_id, role=s.active_role)))


@router.post("/{claim_id}/ai-summary")
async def summary(claim_id: str, request: Request, s: Session = Depends(_staff)) -> dict:
    c = await _get(request, s, claim_id)
    try:
        await ck.ai_summary(_store(request), s.tenant_id, c, by=s.user_id, role=s.active_role)
    except KillSwitchActive:
        raise HTTPException(status_code=423, detail="The claims assistant is switched off (kill switch).") from None
    except BrainError as e:
        raise HTTPException(status_code=429, detail=f"AI summary unavailable: {type(e).__name__}.") from None
    return _view(c)


class NoteBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    client_visible: bool = False


@router.post("/{claim_id}/notes")
async def note(claim_id: str, body: NoteBody, request: Request, s: Session = Depends(_staff)) -> dict:
    store, c = _store(request), await _get(request, s, claim_id)
    if body.client_visible:  # client-visible words go through the coverage guard + compliance release
        rel = await try_release(store=store, tenant_id=s.tenant_id, draft=body.text,
                                context={"matter_kind": "claim", "claim_reference": c["claim_reference"],
                                         "client_context_summary": c.get("client_name") or ""},
                                author_agent="operator", matter_id=None, envelope_id=str(uuid.uuid4()), client_id=c["client_id"],
                                mocked_channels_used=[])
        if rel.verdict != "release":
            return {"released": False, "verdict": rel.verdict, "reason": getattr(rel, "reason", None),
                    "blocked_spans": getattr(rel, "spans", None) or [], "recommended_rewrite": getattr(rel, "recommended_rewrite", None)}
    async with use_tenant(s.tenant_id):
        u = await wrap(store).users.find_one({"user_id": s.user_id}) or {}
    await ck.add_note(store, s.tenant_id, c, text=body.text, client_visible=body.client_visible, by=s.user_id,
                      by_name=u.get("display_name") or s.active_role, role=s.active_role)
    return {"released": True, **_view(await _get(request, s, claim_id))}


@router.post("/{claim_id}/files")
async def upload(claim_id: str, request: Request, file: UploadFile = File(...), kind: str = Form("other"),
                 client_visible: bool = Form(True), keep_location: bool = Form(False), s: Session = Depends(_staff)) -> dict:
    c = await _get(request, s, claim_id)
    doc = await _run(ck.add_file(_store(request), s.tenant_id, c, kind=kind, data=await file.read(), filename=file.filename or "upload",
                                 content_type=file.content_type or "", keep_location=keep_location, by=s.user_id, role=s.active_role,
                                 client_visible=client_visible))
    return {"file": ck._doc_out(doc)}


@router.get("/{claim_id}/files/{file_id}")
async def download(claim_id: str, file_id: str, request: Request, s: Session = Depends(_staff)) -> Response:
    c = await _get(request, s, claim_id)
    d = next((d for d in c.get("docs") or [] if d["file_id"] == file_id), None)
    if not d:
        raise HTTPException(status_code=404, detail="file_not_found")
    data, ct = await object_store.get_object(d["storage_path"])
    return Response(data, media_type=d["content_type"] or ct, headers={"Content-Disposition": f'inline; filename="{d["filename"]}"'})


@router.post("/{claim_id}/pack")
async def pack(claim_id: str, request: Request, s: Session = Depends(_staff)) -> dict:
    c = await _get(request, s, claim_id)
    await ck.build_pack(_store(request), s.tenant_id, c, by=s.user_id, role=s.active_role)
    return _view(c)


@router.get("/{claim_id}/pack.{ext}")
async def pack_file(claim_id: str, ext: str, request: Request, s: Session = Depends(_staff)) -> Response:
    c = await _get(request, s, claim_id)
    if not c.get("pack") or ext not in ("pdf", "zip"):
        raise HTTPException(status_code=404, detail="Generate the claim pack first.")
    data, _ = await object_store.get_object(c["pack"][f"{ext}_path"])
    return Response(data, media_type="application/pdf" if ext == "pdf" else "application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{c["claim_reference"]}-claim-pack.{ext}"'})


class SubmissionBody(BaseModel):
    insurer_claim_no: str = Field(max_length=60)
    lodged_on: str
    contact: str = Field(default="", max_length=200)


@router.post("/{claim_id}/submission")
async def submission(claim_id: str, body: SubmissionBody, request: Request, s: Session = Depends(_staff)) -> dict:
    c = await _get(request, s, claim_id)
    return _view(await _run(ck.record_submission(_store(request), s.tenant_id, c, insurer_claim_no=body.insurer_claim_no,
                                                 lodged_on=body.lodged_on, contact=body.contact, by=s.user_id, role=s.active_role)))


@router.post("/{claim_id}/submit-mocked")
async def submit_mocked(claim_id: str, request: Request, s: Session = Depends(_staff)) -> dict:
    c = await _get(request, s, claim_id)
    return _view(await _run(ck.submit_mocked(_store(request), s.tenant_id, c, by=s.user_id, role=s.active_role)))


class AssessBody(BaseModel):
    assessor: str = Field(default="", max_length=200)
    repairer: str = Field(default="", max_length=200)
    appointments: list[dict] = Field(default_factory=list, max_length=10)


@router.post("/{claim_id}/assessing")
async def assessing(claim_id: str, body: AssessBody, request: Request, s: Session = Depends(_staff)) -> dict:
    c = await _get(request, s, claim_id)
    return _view(await _run(ck.set_assessing(_store(request), s.tenant_id, c, assessor=body.assessor, repairer=body.repairer,
                                             appointments=body.appointments, by=s.user_id, role=s.active_role)))


class DecisionBody(BaseModel):
    outcome: str
    reason: str = Field(default="", max_length=2000)
    letter_file_id: str
    amount: str | None = None


@router.post("/{claim_id}/decision")
async def decision(claim_id: str, body: DecisionBody, request: Request, s: Session = Depends(_staff)) -> dict:
    c = await _get(request, s, claim_id)
    return _view(await _run(ck.record_decision(_store(request), s.tenant_id, c, outcome=body.outcome, reason=body.reason,
                                               letter_file_id=body.letter_file_id, amount=body.amount, by=s.user_id, role=s.active_role)))


class SettleBody(BaseModel):
    amount: str
    excess_applied: str = "0"
    payee: str
    payee_name: str = Field(default="", max_length=200)
    method: str
    txn_id: str | None = None


@router.post("/{claim_id}/settle")
async def settle(claim_id: str, body: SettleBody, request: Request, s: Session = Depends(_staff)) -> dict:
    c = await _get(request, s, claim_id)
    return _view(await _run(ck.settle(_store(request), s.tenant_id, c, amount=body.amount, excess_applied=body.excess_applied,
                                      payee=body.payee, payee_name=body.payee_name, method=body.method, txn_id=body.txn_id,
                                      by=s.user_id, role=s.active_role)))


class ReasonBody(BaseModel):
    reason: str = Field(default="", max_length=500)


@router.post("/{claim_id}/close")
async def close(claim_id: str, request: Request, body: ReasonBody = Body(default=ReasonBody()), s: Session = Depends(_staff)) -> dict:
    c = await _get(request, s, claim_id)
    return _view(await _run(ck.close(_store(request), s.tenant_id, c, note=body.reason or None, by=s.user_id, role=s.active_role)))


@router.post("/{claim_id}/withdraw")
async def withdraw(claim_id: str, body: ReasonBody, request: Request, s: Session = Depends(_staff)) -> dict:
    c = await _get(request, s, claim_id)
    return _view(await _run(ck.withdraw(_store(request), s.tenant_id, c, reason=body.reason, by=s.user_id, role=s.active_role)))
