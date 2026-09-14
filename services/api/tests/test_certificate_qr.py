"""Phase 16 — server-side QR endpoint tests.

Verifies:
  * `GET /api/c/certificate/{id}/qr.png` returns image/png bytes with
    a real PNG signature.
  * The QR decodes (via zxing-cpp) to the exact JSON payload spec:
    tenant, cert_id, issued_at, chain_tip.
  * Payload is deterministic: same event history → same bytes.
"""
from __future__ import annotations

import io
import json
import uuid
from datetime import datetime, timezone

import pytest


pytestmark = pytest.mark.asyncio


PNG_SIG = b"\x89PNG\r\n\x1a\n"


async def _seed_a_certificate(pool, tenant_id, client_id):
    """Append a CertificateRegenerated event so the QR route has a
    context to render. Returns the certificate_id.
    """
    from app.kernel.trail import append
    cert_id = str(uuid.uuid4())
    policy_id = str(uuid.uuid4())
    envelope_id = str(uuid.uuid4())
    issued_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    await append(
        pool, tenant_id, "CertificateRegenerated",
        actor={"kind": "system", "id": "certificate", "name": "certificate"},
        payload={
            "envelope_id": envelope_id,
            "policy_id": policy_id,
            "certificate_id": cert_id,
            "effective_date": issued_at,
            "certificate": {
                "certificate_id": cert_id,
                "policy_id": policy_id,
                "insurer_display": "Fictional Re",
                "product": "General Liability",
                "issued_at": issued_at,
                "mocked_insurer": True,
            },
            "mocked": True,
        },
        mocked=True,
        idempotency_key=f"cert:regen:{cert_id}",
    )
    return cert_id


def _decode_qr(png_bytes: bytes) -> str:
    """Decode a QR PNG to its text payload using zxing-cpp."""
    import zxingcpp
    from PIL import Image
    img = Image.open(io.BytesIO(png_bytes))
    results = zxingcpp.read_barcodes(img)
    assert results, "zxing-cpp found no barcodes in the PNG"
    return results[0].text


async def test_qr_endpoint_returns_real_png(app_client, scene_seeded):
    _, client = app_client
    owner = next(
        c for c in scene_seeded["clients"] if c["role"] == "client_owner"
    )
    r = await client.post(
        "/api/auth/dev/impersonate", json={"email": owner["email"]}
    )
    assert r.status_code == 200

    app = app_client[0]
    cert_id = await _seed_a_certificate(
        app.state.pool,
        scene_seeded["tenant_id"],
        scene_seeded["client_id"],
    )

    resp = await client.get(f"/api/c/certificate/{cert_id}/qr.png")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content.startswith(PNG_SIG)
    assert int(resp.headers.get("x-qr-payload-length", "0")) > 0


async def test_qr_payload_decodes_to_spec(app_client, scene_seeded):
    _, client = app_client
    owner = next(
        c for c in scene_seeded["clients"] if c["role"] == "client_owner"
    )
    await client.post(
        "/api/auth/dev/impersonate", json={"email": owner["email"]}
    )
    app = app_client[0]
    cert_id = await _seed_a_certificate(
        app.state.pool,
        scene_seeded["tenant_id"],
        scene_seeded["client_id"],
    )
    resp = await client.get(f"/api/c/certificate/{cert_id}/qr.png")
    assert resp.status_code == 200
    text = _decode_qr(resp.content)
    payload = json.loads(text)
    assert set(payload.keys()) == {
        "tenant", "cert_id", "issued_at", "chain_tip"
    }
    assert payload["cert_id"] == cert_id
    assert payload["tenant"] == "burmac"
    assert isinstance(payload["chain_tip"], str) and len(payload["chain_tip"]) == 64


async def test_qr_bytes_are_deterministic(app_client, scene_seeded):
    _, client = app_client
    owner = next(
        c for c in scene_seeded["clients"] if c["role"] == "client_owner"
    )
    await client.post(
        "/api/auth/dev/impersonate", json={"email": owner["email"]}
    )
    app = app_client[0]
    cert_id = await _seed_a_certificate(
        app.state.pool,
        scene_seeded["tenant_id"],
        scene_seeded["client_id"],
    )
    r1 = await client.get(f"/api/c/certificate/{cert_id}/qr.png")
    r2 = await client.get(f"/api/c/certificate/{cert_id}/qr.png")
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.content == r2.content, (
        "same cert_id + same chain tip must yield byte-identical QR PNG"
    )


async def test_qr_unknown_cert_404s(app_client, scene_seeded):
    _, client = app_client
    owner = next(
        c for c in scene_seeded["clients"] if c["role"] == "client_owner"
    )
    await client.post(
        "/api/auth/dev/impersonate", json={"email": owner["email"]}
    )
    r = await client.get(f"/api/c/certificate/{uuid.uuid4()}/qr.png")
    assert r.status_code == 404
