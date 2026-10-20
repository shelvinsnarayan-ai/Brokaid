"""R2.0 — click-to-accept (client) and signed-offline (staff) routes. Not a digital signature."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import ConfigDict, BaseModel, Field

from app.kernel import acceptance as acc
from app.kernel import renewals as rn
from app.kernel.authority import require_authority
from app.middleware.security import client_ip
from app.middleware.session import require_operator_session, require_session

c_router = APIRouter(prefix="/api/c/acceptance", tags=["acceptance"])
o_router = APIRouter(prefix="/api/o/acceptance", tags=["acceptance"])
STAFF = ("operator", "principal", "ae")


def _store(request: Request):
    return request.app.state.mongo_db


def _client(s=Depends(require_session)):
    if s.active_role not in ("client_owner", "client_contact"):
        raise HTTPException(status_code=403, detail="client_role_required")
    return s


def _staff(s=Depends(require_operator_session)):
    if s.active_role not in STAFF:
        raise HTTPException(status_code=403, detail="staff_role_required")
    return s


async def _run(coro):
    try:
        return await coro
    except acc.Bad as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from None
    except rn.Bad as e:
        raise HTTPException(status_code=409, detail=str(e)) from None


def _view(doc: dict, rec: dict | None) -> dict:
    return {"kind": doc["kind"], "ref_id": doc["ref_id"], "title": doc["title"], "lines": doc["lines"], "doc_sha256": doc["sha256"],
            "ready": doc["subject"]["ready"], "label": acc.LABEL, "accepted": acc.public(rec)}


async def _complete(store, tid: str, kind: str, ref_id: str, rec: dict, by: str, role: str) -> dict:
    """Run the step the signature used to unlock: execute the endorsement + certificate, or bind the renewal."""
    if kind == "renewal":
        r = await rn.get(store, tid, ref_id)
        if rn.cur(r) == "paid":
            r = await _run(rn.accepted(store, tid, r, rec, by=by, role=role))
        elif rn.cur(r) == "lapsed" and rec.get("late"):
            r = await _run(rn.late_accepted(store, tid, r, rec, by=by, role=role))
        return {"stage": r["stage"], "new_policy_id": r.get("new_policy_id"), "verifier_token": r.get("verifier_token")}
    from app.kernel.certificate import regenerate_certificate_dispatch
    from app.kernel.endorsement import execute_endorsement
    from app.db.tenant_safe import wrap
    from app.kernel.tenant_ctx import use_tenant
    async with use_tenant(tid):
        if await wrap(store).events.find_one({"type": "CertificateRegenerated", "payload.envelope_id": ref_id}):
            return {"state": "certificate_ready"}
    await execute_endorsement(store=store, tenant_id=tid, envelope_id=ref_id, policy_id=rec["policy_id"],
                              approver_actor={"kind": "system", "id": "endorsement", "name": "endorsement"})
    cert = await regenerate_certificate_dispatch(store=store, tenant_id=tid, policy_id=rec["policy_id"], envelope_id=ref_id)
    return {"state": "certificate_ready", "certificate_id": cert["certificate"]["certificate_id"], "verifier_token": cert["raw_token"]}


class AcceptBody(BaseModel):
    model_config = ConfigDict(extra="forbid")  # R2.6: unknown fields are a 422
    typed_name: str = Field(min_length=1, max_length=120)
    accepted: bool
    doc_sha256: str = Field(min_length=64, max_length=64)
    confirm_name: bool = False


async def _show(request, s, kind, ref_id):
    store = _store(request)
    doc = await _run(acc.document(store, s.tenant_id, kind, ref_id))
    return _view(doc, await acc.recorded(store, s.tenant_id, doc["envelope_id"]))


async def _pdf(request, s, kind, ref_id):
    doc = await _run(acc.document(_store(request), s.tenant_id, kind, ref_id))
    return Response(await acc.pdf_bytes(doc), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{kind}-{ref_id[:8]}-{doc["sha256"][:12]}.pdf"',
                             "X-Document-SHA256": doc["sha256"]})


async def _accept(request, s, kind, ref_id, body: AcceptBody):
    store, tid = _store(request), s.tenant_id
    subj = await _run(acc.subject(store, tid, kind, ref_id))
    await require_authority(store, s, client_id=subj["client_id"], action=f"accept_{kind}_attempt", needed="can_sign",
                            policy_id=subj["policy_id"], envelope_id=subj["envelope_id"])
    try:
        rec = await acc.accept(store, tid, kind, ref_id, session=s, typed_name=body.typed_name, accepted=body.accepted,
                               doc_sha256=body.doc_sha256, confirm_name=body.confirm_name, ip=client_ip(request),
                               user_agent=request.headers.get("user-agent"))
    except acc.Bad as e:
        if e.detail == "name_mismatch":
            raise HTTPException(status_code=409, detail={"code": "name_mismatch", "detail":
                                "That name doesn't match the name we hold for you. Check it, or confirm it's correct."}) from None
        raise HTTPException(status_code=e.status, detail=e.detail) from None
    done = await _complete(store, tid, kind, ref_id, rec, s.user_id, s.active_role)
    return {"accepted": acc.public(rec), **done}


@c_router.get("/endorsement/{envelope_id}")
async def c_endorsement(envelope_id: str, request: Request, s=Depends(_client)) -> dict:
    return await _show(request, s, "endorsement", envelope_id)


@c_router.get("/endorsement/{envelope_id}/pdf")
async def c_endorsement_pdf(envelope_id: str, request: Request, s=Depends(_client)):
    return await _pdf(request, s, "endorsement", envelope_id)


@c_router.post("/endorsement/{envelope_id}")
async def c_endorsement_accept(envelope_id: str, body: AcceptBody, request: Request, s=Depends(_client)) -> dict:
    return await _accept(request, s, "endorsement", envelope_id, body)


@c_router.get("/renewal/{renewal_id}")
async def c_renewal(renewal_id: str, request: Request, s=Depends(_client)) -> dict:
    return await _show(request, s, "renewal", renewal_id)


@c_router.get("/renewal/{renewal_id}/pdf")
async def c_renewal_pdf(renewal_id: str, request: Request, s=Depends(_client)):
    return await _pdf(request, s, "renewal", renewal_id)


@c_router.post("/renewal/{renewal_id}")
async def c_renewal_accept(renewal_id: str, body: AcceptBody, request: Request, s=Depends(_client)) -> dict:
    return await _accept(request, s, "renewal", renewal_id, body)


# ---- staff ----
def _kind(kind: str) -> str:
    if kind not in acc.KINDS:
        raise HTTPException(status_code=404, detail="not_found")
    return kind


@o_router.get("/{kind}/{ref_id}")
async def o_show(kind: str, ref_id: str, request: Request, s=Depends(_staff)) -> dict:
    return await _show(request, s, _kind(kind), ref_id)


@o_router.get("/{kind}/{ref_id}/pdf")
async def o_pdf(kind: str, ref_id: str, request: Request, s=Depends(_staff)):
    return await _pdf(request, s, _kind(kind), ref_id)


@o_router.post("/{kind}/{ref_id}/offline")
async def o_offline(kind: str, ref_id: str, request: Request, file: UploadFile = File(...), reason: str = Form(...),
                    s=Depends(_staff)) -> dict:
    store, tid = _store(request), s.tenant_id
    data = await file.read(acc.MAX_SCAN + 1)
    rec = await _run(acc.offline(store, tid, _kind(kind), ref_id, data=data, filename=file.filename or "scan", reason=reason,
                                 by=s.user_id, role=s.active_role))
    return {"accepted": acc.public(rec), **await _complete(store, tid, kind, ref_id, rec, s.user_id, s.active_role)}
