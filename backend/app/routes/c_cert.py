"""Client-facing certificate QR endpoint (R5c-4b — Mongo-only).

Endpoint
--------
GET /api/c/certificate/{certificate_id}/qr.png

Payload (compact JSON, phase-16 decision lock):
    {"tenant": ..., "cert_id": ..., "issued_at": ..., "chain_tip": ...}

Also mounts POST /regen for E2E scenes — kicks off cert regen +
verifier_token issuance.
"""
from __future__ import annotations

import io
import json
from typing import Any

import qrcode
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from app.auth.sessions import Session
from app.kernel import certificate_mongo
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


def render_qr_png(payload_text: str) -> bytes:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8, border=4,
    )
    qr.add_data(payload_text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0B3B4B", back_color="#F4EFE6")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@router.get("/{certificate_id}/qr.png")
async def certificate_qr(
    certificate_id: str, request: Request,
    session: Session = Depends(require_session),
) -> Response:
    ctx = await _load_cert_context(request, session.tenant_id, certificate_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail="certificate_not_found")
    payload_text = build_qr_payload(ctx)
    png = render_qr_png(payload_text)
    return Response(
        content=png, media_type="image/png",
        headers={
            "Cache-Control": "no-store",
            "X-QR-Payload-Length": str(len(payload_text)),
        },
    )


class RegenRequest(BaseModel):
    policy_id: str


@router.post("/regen")
async def regen(
    body: RegenRequest, request: Request,
    session: Session = Depends(require_session),
) -> dict:
    """Regenerate a certificate + issue verifier token. Client-role only."""
    if session.active_role not in ("client_owner", "client_contact"):
        raise HTTPException(status_code=403, detail="client_role_required")
    try:
        result = await certificate_mongo.regenerate_certificate(
            store=_store(request),
            tenant_id=session.tenant_id,
            policy_id=body.policy_id,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="policy_not_found")
    return {
        "certificate": result["certificate"],
        "expires_at": result["expires_at"],
    }
