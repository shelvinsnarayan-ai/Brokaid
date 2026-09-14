"""Phase 16 — server-side QR encoder for the certificate.

Endpoint
--------
GET /api/c/certificate/{certificate_id}/qr.png

Returns a real PNG-encoded QR generated with the `qrcode` library.
The QR payload is compact JSON per the phase-16 decision lock:

    {
        "tenant": "<slug>",
        "cert_id": "<uuid>",
        "issued_at": "<iso>",
        "chain_tip": "<latest event_hash at issue time>"
    }

Rationale: The payload is deterministic and testable — a decoder can
recover the exact same JSON that was encoded. No PDF surface in this
phase; the frontend swaps its SVG placeholder for a plain `<img>` at
this URL.
"""
from __future__ import annotations

import io
import json
from typing import Any

import qrcode
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from app.auth.sessions import Session
from app.middleware.session import require_session


router = APIRouter(prefix="/api/c/certificate")


async def _load_cert_context(
    pool, tenant_id: str, certificate_id: str,
) -> dict[str, Any] | None:
    """Pull the cert issue event + latest event_hash (chain tip)."""
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            cert_row = await c.fetchrow(
                """
                SELECT payload, event_hash
                  FROM events
                 WHERE tenant_id=$1::uuid
                   AND type='CertificateRegenerated'
                   AND payload->>'certificate_id' = $2
                 ORDER BY seq DESC LIMIT 1
                """,
                tenant_id, certificate_id,
            )
            tenant_row = await c.fetchrow(
                "SELECT slug FROM tenants WHERE tenant_id=$1::uuid", tenant_id,
            )
            tip_row = await c.fetchrow(
                "SELECT event_hash FROM events WHERE tenant_id=$1::uuid "
                "ORDER BY seq DESC LIMIT 1",
                tenant_id,
            )
    if cert_row is None or tenant_row is None:
        return None
    payload = cert_row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    cert = payload.get("certificate") or {}
    return {
        "tenant": tenant_row["slug"],
        "cert_id": certificate_id,
        "issued_at": cert.get("issued_at") or payload.get("effective_date"),
        "chain_tip": tip_row["event_hash"] if tip_row else None,
    }


def build_qr_payload(ctx: dict[str, Any]) -> str:
    """Deterministic compact JSON encoding of the QR payload."""
    return json.dumps({
        "tenant": ctx["tenant"],
        "cert_id": ctx["cert_id"],
        "issued_at": ctx["issued_at"],
        "chain_tip": ctx["chain_tip"],
    }, separators=(",", ":"), sort_keys=True)


def render_qr_png(payload_text: str) -> bytes:
    """Render a QR PNG with fixed geometry so pixel output is
    deterministic run-to-run (same input → same bytes).
    """
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=4,
    )
    qr.add_data(payload_text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0B3B4B", back_color="#F4EFE6")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@router.get("/{certificate_id}/qr.png")
async def certificate_qr(
    certificate_id: str,
    request: Request,
    session: Session = Depends(require_session),
) -> Response:
    ctx = await _load_cert_context(
        request.app.state.pool, session.tenant_id, certificate_id,
    )
    if ctx is None:
        raise HTTPException(status_code=404, detail="certificate_not_found")
    payload_text = build_qr_payload(ctx)
    png = render_qr_png(payload_text)
    return Response(
        content=png,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store",
            "X-QR-Payload-Length": str(len(payload_text)),
        },
    )
