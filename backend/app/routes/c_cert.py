"""Client-facing certificate endpoints (R1 #3 — Build Brief v2.1 §8).

Endpoints
---------
GET  /api/c/certificate/{certificate_id}/qr.png?t={raw_token}
        Verifier-URL QR (Meridian palette). If `t` is present and its
        hash matches the certificate's stored hash, encode the deep
        link `{verify_origin}/verify/{token}`; else fall back to the
        internal chain-tip JSON.

POST /api/c/certificate/{certificate_id}/pdf
        Server-side PDF (Meridian light expression) with embedded QR.
        Body: {raw_token, verify_origin}. Server validates the token
        hash matches the certificate's stored hash.

POST /api/c/certificate/regen
        Regenerate a certificate + issue verifier token. Now accepts
        optional `holder` and `requirements` from the "Get a
        certificate" client flow.
"""
from __future__ import annotations

import io
import json
import uuid
from typing import Any, Optional

import qrcode
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.auth.sessions import Session
from app.kernel import cert_stp, certificate_mongo, scene
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch
from app.kernel.workflow import enqueue as queue_enqueue
from app.kernel.authority import require_client_authority
from app.middleware.session import require_session


router = APIRouter(prefix="/api/c/certificate")


def _store(request: Request):
    s = getattr(request.app.state, "mongo_db", None)
    if s is None:
        raise HTTPException(status_code=503, detail="mongo_store_unavailable")
    return s


async def _load_cert_context(
    request: Request, tenant_id: str, certificate_id: str,
) -> dict[str, Any] | None:
    return await certificate_mongo.load_cert_context(
        _store(request), tenant_id, certificate_id,
    )


def build_qr_payload(ctx: dict[str, Any]) -> str:
    return json.dumps({
        "tenant": ctx["tenant"],
        "cert_id": ctx["cert_id"],
        "issued_at": ctx["issued_at"],
        "chain_tip": ctx["chain_tip"],
    }, separators=(",", ":"), sort_keys=True)


def render_qr_png(payload_text: str, *, size: int = 8) -> bytes:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=size, border=4,
    )
    qr.add_data(payload_text)
    qr.make(fit=True)
    # Meridian light: Deep Navy on Cloud.
    img = qr.make_image(fill_color="#0F1F3D", back_color="#F6F7FA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _validate_verify_origin(origin: str | None) -> str:
    if not origin:
        return ""
    origin = origin.strip()
    if not (origin.startswith("https://") or origin.startswith("http://")):
        return ""
    if len(origin) > 200:
        return ""
    return origin.rstrip("/")


def _token_matches(hashed: str, expected_hash: str) -> bool:
    return bool(hashed) and hashed == expected_hash


@router.get("/{certificate_id}/qr.png")
async def certificate_qr(
    certificate_id: str, request: Request,
    session: Session = Depends(require_session),
    t: Optional[str] = None,
    o: Optional[str] = None,
) -> Response:
    ctx = await _load_cert_context(request, session.tenant_id, certificate_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail="certificate_not_found")

    payload_text = build_qr_payload(ctx)
    used_deeplink = False
    if t:
        hashed = certificate_mongo.hash_token(t)
        cert_hash = (ctx.get("certificate") or {}).get("qr_token_hash")
        # cert dict doesn't hold token hash; look up verifier_tokens row.
        row = await certificate_mongo.load_cert_by_token_hash(
            _store(request), session.tenant_id, hashed,
        )
        if row and row.get("certificate_id") == certificate_id:
            origin = _validate_verify_origin(o) or _validate_verify_origin(
                str(request.base_url)
            )
            payload_text = f"{origin}/verify/{t}"
            used_deeplink = True
    png = render_qr_png(payload_text)
    return Response(
        content=png, media_type="image/png",
        headers={
            "Cache-Control": "no-store",
            "X-QR-Payload-Length": str(len(payload_text)),
            "X-QR-Kind": "deeplink" if used_deeplink else "chain-tip",
        },
    )


class RegenHolder(BaseModel):
    name: str | None = None
    address: str | None = None
    email: str | None = None


class RegenRequirements(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    interest_type: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    special_requirements: str | None = Field(
        default=None,
        validation_alias=AliasChoices("special_requirements", "special"),
    )


class RegenRequest(BaseModel):
    policy_id: str
    holder: RegenHolder | None = None
    requirements: RegenRequirements | None = None


@router.post("/regen")
async def regen(
    body: RegenRequest, request: Request,
    session: Session = Depends(require_session),
) -> dict:
    if session.active_role not in ("client_owner", "client_contact"):
        raise HTTPException(status_code=403, detail="client_role_required")
    await require_client_authority(_store(request), session, action="certificate_attempt", needed="can_certificates")
    gate = await _r_coc_01(request, session, body)
    if gate is not None:
        return gate
    try:
        result = await certificate_mongo.regenerate_certificate(
            store=_store(request),
            tenant_id=session.tenant_id,
            policy_id=body.policy_id,
            holder=body.holder.model_dump() if body.holder else None,
            requirements=body.requirements.model_dump() if body.requirements else None,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="policy_not_found")
    return {
        "certificate": result["certificate"],
        "raw_token": result["raw_token"],
        "expires_at": result["expires_at"],
        "ledger_event_id": result.get("ledger_event_id"),
    }


async def _r_coc_01(request: Request, session: Session, body: "RegenRequest") -> JSONResponse | None:
    """R-CoC-01: STP only when all five conditions pass; else a broker task (202)."""
    store = _store(request)
    policy = await scene.load_policy(store, session.tenant_id, body.policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="policy_not_found")
    f = await cert_stp.facts(store, session.tenant_id, body.policy_id)
    special = body.requirements.special_requirements if body.requirements else None
    reasons = cert_stp.evaluate(policy, special=special, **f)
    if not reasons:
        return None
    request_id = str(uuid.uuid4())
    async with use_tenant(session.tenant_id):
        await append_via_dispatch(
            store, session.tenant_id, "CertificateRequestNeedsBroker",
            actor={"kind": "human", "id": session.user_id, "name": session.active_role},
            payload={"envelope_id": request_id, "policy_id": body.policy_id,
                     "reasons": reasons, "holder": body.holder.model_dump() if body.holder else None,
                     "special_requirements": special},
            idempotency_key=f"cert:needs_broker:{request_id}",
        )
    proposal_ref = await scene.last_event_id_for_envelope(
        store, session.tenant_id, "CertificateRequestNeedsBroker", request_id)
    await queue_enqueue(
        store, tenant_id=session.tenant_id, envelope_id=request_id,
        class_key="certificate_issue", proposal_ref=proposal_ref or "",
        matter_id=None, client_id=policy["client_id"],
        gate_reason="certificate_broker_task", confidence=None,
        routed_to_role="account_executive",
    )
    return JSONResponse(status_code=202, content={
        "status": "awaiting_broker", "request_id": request_id, "reasons": reasons,
        "reasons_plain": [cert_stp.PLAIN[r] for r in reasons],
    })


class PdfRequest(BaseModel):
    raw_token: str
    verify_origin: str | None = None


@router.post("/{certificate_id}/pdf")
async def certificate_pdf(
    certificate_id: str, body: PdfRequest, request: Request,
    session: Session = Depends(require_session),
) -> Response:
    """Server-side branded PDF with embedded QR encoding the /verify link."""
    if session.active_role not in ("client_owner", "client_contact"):
        raise HTTPException(status_code=403, detail="client_role_required")
    await require_client_authority(_store(request), session, action="certificate_attempt", needed="can_certificates")

    ctx = await _load_cert_context(request, session.tenant_id, certificate_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail="certificate_not_found")

    hashed = certificate_mongo.hash_token(body.raw_token)
    row = await certificate_mongo.load_cert_by_token_hash(
        _store(request), session.tenant_id, hashed,
    )
    if not row or row.get("certificate_id") != certificate_id:
        raise HTTPException(status_code=403, detail="token_mismatch")

    origin = _validate_verify_origin(body.verify_origin) or _validate_verify_origin(
        str(request.base_url)
    )
    verify_url = f"{origin}/verify/{body.raw_token}"
    from app.services.cert_pdf import render_certificate_pdf
    pdf_bytes = render_certificate_pdf(
        certificate=ctx["certificate"],
        verify_url=verify_url,
        ledger_event_id=ctx.get("event_id"),
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": (
                f'attachment; filename="certificate-{certificate_id[:8]}.pdf"'
            ),
        },
    )
