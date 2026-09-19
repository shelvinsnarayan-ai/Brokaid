"""Phase R5a-2a — Scene 4 E2E under Mongo: cert regen + QR round-trip.

Uses the fixture from `test_queue_e2e_mongo.py` (`mongo_e2e`) and adds:
  - Client-role user + session under tenant A.
  - Seed a policy + client in Mongo tenant A.
  - POST /api/c/certificate/regen → 200 + certificate returned.
  - Verify verifier_tokens row landed in Mongo under tenant A.
  - GET /api/c/certificate/{id}/qr.png → PNG returned.
  - Decode PNG via zxing-cpp → assert payload is
    {tenant, cert_id, issued_at, chain_tip} and chain_tip matches
    current Mongo tenant tip.
  - RBAC negatives: auditor regen → 403.
"""
from __future__ import annotations

import io
import json
import uuid

import numpy as np
import pytest
import pytest_asyncio
import zxingcpp
from PIL import Image

from app.auth.sessions import COOKIE_NAME

pytestmark = pytest.mark.asyncio

# R5a-2a: reuse the mongo_e2e fixture defined in the sibling test file.
# pytest fixture inheritance across files requires an explicit plugin
# declaration.
pytest_plugins = ["tests.test_queue_e2e_mongo"]


def _cookie(sid: str) -> dict[str, str]:
    return {COOKIE_NAME: sid}


@pytest_asyncio.fixture
async def scene4_ctx(mongo_e2e):
    """Extends `mongo_e2e` with a client + policy in tenant A + sessions."""
    import uuid as _uuid
    from app.db.tenant_safe import wrap
    from app.kernel import identity
    from app.kernel.tenant_ctx import use_tenant

    safe = wrap(mongo_e2e["mongo"])
    tenant_a = mongo_e2e["tenant_a"]

    # Seed a client + policy under tenant A.
    client_id = str(_uuid.uuid4())
    policy_id = str(_uuid.uuid4())
    async with use_tenant(tenant_a):
        await safe.clients.insert_one({
            "client_id": client_id, "display_name": "Ripple Constructions Pty Ltd",
            "status": "active",
        })
        await safe.policies.insert_one({
            "policy_id": policy_id, "client_id": client_id,
            "product": "public_liability",
            "schedule": {"cover_types": ["public_liability"],
                         "excess": "AUD 2,500",
                         "territorial_limits": "Australia"},
            "premium_amount": "12500.00", "premium_currency": "AUD",
            "status": "managed",
            "insurer_display": "Landmark Insurance (mocked)",
            "inception": "2025-01-01T00:00:00.000000+00:00",
            "expiry": "2026-01-01T00:00:00.000000+00:00",
        })

    # Seed a client_owner user under tenant A.
    client_user_id = str(_uuid.uuid4())
    async with use_tenant(tenant_a):
        await safe.users.insert_one({
            "user_id": client_user_id, "email": "own-a@ci.example",
            "display_name": "Ripple Owner", "kind": "client",
            "password_hash": None, "mfa_secret": None,
        })
        await safe.user_client_links.insert_one({
            "user_id": client_user_id, "client_id": client_id,
            "role": "client_owner",
        })
    client_sess = await identity.create_session(
        mongo_e2e["mongo"], user_id=client_user_id, tenant_id=tenant_a,
        client_id=client_id, active_role="client_owner", mfa_verified=True,
    )

    yield {
        **mongo_e2e,
        "policy_id": policy_id, "client_id": client_id,
        "client_user_id": client_user_id,
        "client_session_id": client_sess.session_id,
    }


def _decode_qr_png(png_bytes: bytes) -> str:
    """Decode a PNG to the string payload it encodes via zxing-cpp."""
    img = Image.open(io.BytesIO(png_bytes)).convert("L")
    arr = np.asarray(img)
    results = zxingcpp.read_barcodes(arr)
    assert len(results) == 1, f"expected 1 QR, got {len(results)}"
    return results[0].text


# ---- Scene 4a: cert regen + verifier_tokens on Mongo ----------------
async def test_r5a2a_scene4_cert_regen_writes_verifier_tokens_on_mongo(
    scene4_ctx,
):
    from app.db.tenant_safe import wrap
    from app.kernel.tenant_ctx import use_tenant

    r = await scene4_ctx["client"].post(
        "/api/c/certificate/regen",
        json={"policy_id": scene4_ctx["policy_id"]},
        cookies=_cookie(scene4_ctx["client_session_id"]),
    )
    assert r.status_code == 200, r.text
    cert = r.json()["certificate"]
    cert_id = cert["certificate_id"]

    # verifier_tokens landed under tenant A only (system read).
    safe = wrap(scene4_ctx["mongo"])
    row = await safe.system("verifier_tokens").find_one({
        "certificate_id": cert_id, "tenant_id": scene4_ctx["tenant_a"],
    })
    assert row is not None
    assert row["policy_id"] == scene4_ctx["policy_id"]
    # Cross-tenant: no row under tenant B.
    row_b = await safe.system("verifier_tokens").find_one({
        "certificate_id": cert_id, "tenant_id": scene4_ctx["tenant_b"],
    })
    assert row_b is None


# ---- Scene 4b: QR PNG round-trip decode ------------------------------
async def test_r5a2a_scene4_qr_png_decodes_to_expected_payload(scene4_ctx):
    r = await scene4_ctx["client"].post(
        "/api/c/certificate/regen",
        json={"policy_id": scene4_ctx["policy_id"]},
        cookies=_cookie(scene4_ctx["client_session_id"]),
    )
    assert r.status_code == 200, r.text
    cert_id = r.json()["certificate"]["certificate_id"]

    r2 = await scene4_ctx["client"].get(
        f"/api/c/certificate/{cert_id}/qr.png",
        cookies=_cookie(scene4_ctx["client_session_id"]),
    )
    assert r2.status_code == 200, r2.text
    assert r2.headers["content-type"] == "image/png"
    png = r2.content
    assert len(png) > 100 and png[:8] == b"\x89PNG\r\n\x1a\n"

    payload_text = _decode_qr_png(png)
    payload = json.loads(payload_text)
    assert set(payload.keys()) == {"tenant", "cert_id", "issued_at", "chain_tip"}
    assert payload["cert_id"] == cert_id
    assert payload["tenant"]  # non-empty slug
    assert payload["chain_tip"]

    # chain_tip matches current Mongo tenant tip.
    from app.kernel.trail_dispatch import tip_hash
    tip = await tip_hash(scene4_ctx["mongo"], scene4_ctx["tenant_a"])
    assert payload["chain_tip"] == tip


# ---- Scene 4c: RBAC negative — auditor cannot regen ------------------
async def test_r5a2a_scene4_auditor_cannot_regen(scene4_ctx):
    r = await scene4_ctx["client"].post(
        "/api/c/certificate/regen",
        json={"policy_id": scene4_ctx["policy_id"]},
        cookies=_cookie(scene4_ctx["session_aud_a"]),
    )
    assert r.status_code == 403, r.text
