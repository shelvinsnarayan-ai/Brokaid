"""Phase R5a-2a — Mongo mirror of certificate regeneration.

Byte-identical output vs `kernel/certificate.py` for the same input:
  - same `certificate_id` scheme (uuid4 — non-deterministic by design,
    but the returned dict has the SAME SHAPE)
  - same `raw_token` shape (32-byte URL-safe secret)
  - same `token_hash` derivation (sha256 hex)
  - same event authorship (CertificateRegenerated + VerifierTokenIssued)
  - same event payload keys/values (mocked=True on the cert event)

The QR PNG rendering is share-of-concern with `routes/c_cert.py`
(`build_qr_payload` + `render_qr_png`) — kept there so the encoder
lives next to its route. Byte-identical PNGs across backends are the
E2E gate.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.db.tenant_safe import wrap
from app.kernel.tenant_ctx import get_tenant_id, use_tenant
from app.kernel.trail_dispatch import append_via_dispatch


TOKEN_TTL = timedelta(days=90)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _load_policy(store, tenant_id: str, policy_id: str) -> Optional[dict]:
    safe_db = wrap(store)
    async with use_tenant(tenant_id):
        row = await safe_db.policies.find_one({"policy_id": policy_id})
    if not row:
        return None
    schedule = row.get("schedule") or {}
    return {
        "policy_id": row["policy_id"],
        "client_id": row["client_id"],
        "product": row.get("product"),
        "schedule": schedule if isinstance(schedule, dict) else {},
        "premium_amount": str(row.get("premium_amount") or "0"),
        "premium_currency": row.get("premium_currency"),
        "status": row.get("status"),
        "insurer_display": row.get("insurer_display"),
        "inception": _iso(row.get("inception")),
        "expiry": _iso(row.get("expiry")),
    }


async def _load_client_display(store, tenant_id: str, client_id: str) -> str:
    safe_db = wrap(store)
    async with use_tenant(tenant_id):
        row = await safe_db.clients.find_one({"client_id": client_id})
    return (row.get("display_name") if row else "") or ""


def _iso(v) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    try:
        return v.isoformat(timespec="microseconds")
    except Exception:
        return str(v)


async def regenerate_certificate(
    *, store, tenant_id: str, policy_id: str,
    envelope_id: str | None = None,
) -> dict[str, Any]:
    policy = await _load_policy(store, tenant_id, policy_id)
    if policy is None:
        raise LookupError(f"policy {policy_id} not found")
    holder = await _load_client_display(store, tenant_id, policy["client_id"])

    certificate_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    raw_token = secrets.token_urlsafe(32)
    hashed = hash_token(raw_token)
    expires_at = now + TOKEN_TTL

    # verifier_tokens write via tenant-safe wrapper (write path).
    safe_db = wrap(store)
    async with use_tenant(tenant_id):
        try:
            await safe_db.verifier_tokens.insert_one({
                "token_hash": hashed, "tenant_id": tenant_id,
                "policy_id": policy_id, "certificate_id": certificate_id,
                "issued_at": now, "expires_at": expires_at,
                "revoked_at": None, "rate_limit_count": 0,
            })
        except Exception:
            pass  # duplicate → idempotent

    certificate = {
        "certificate_id": certificate_id,
        "policy_id": policy_id,
        "holder_display": holder,
        "insurer_display": policy["insurer_display"],
        "product": policy["product"],
        "cover_types": policy["schedule"].get("cover_types") or [],
        "excess": policy["schedule"].get("excess"),
        "territorial_limits": policy["schedule"].get("territorial_limits"),
        "effective_from": policy["inception"],
        "effective_to": policy["expiry"],
        "issued_at": now.isoformat(timespec="microseconds"),
        "mocked_insurer": True,
    }
    async with use_tenant(tenant_id):
        await append_via_dispatch(
            store, tenant_id, "CertificateRegenerated",
            actor={"kind": "system", "id": "certificate", "name": "certificate"},
            payload={
                "envelope_id": envelope_id,
                "policy_id": policy_id,
                "certificate_id": certificate_id,
                "qr_token_hash": hashed,
                "effective_date": now.isoformat(timespec="microseconds"),
                "certificate": certificate,
                "mocked": True,
            },
            idempotency_key=f"cert:regen:{certificate_id}",
            mocked=True,
        )
        await append_via_dispatch(
            store, tenant_id, "VerifierTokenIssued",
            actor={"kind": "system", "id": "verifier", "name": "verifier"},
            payload={
                "envelope_id": envelope_id,
                "certificate_id": certificate_id,
                "policy_id": policy_id,
                "token_hash": hashed,
                "expires_at": expires_at.isoformat(timespec="microseconds"),
            },
            idempotency_key=f"verifier:token:{hashed}",
        )
    return {
        "certificate": certificate,
        "raw_token": raw_token,
        "token_hash": hashed,
        "expires_at": expires_at.isoformat(timespec="microseconds"),
    }


async def revoke_token(store, tenant_id: str, token_hash: str) -> bool:
    safe_db = wrap(store)
    async with use_tenant(tenant_id):
        r = await safe_db.verifier_tokens.update_one(
            {"token_hash": token_hash, "revoked_at": None},
            {"$set": {"revoked_at": datetime.now(timezone.utc)}},
        )
    return r.modified_count == 1


# ---- Route-side helpers (moved so c_cert can dispatch) ---------------
async def load_cert_context(
    store, tenant_id: str, certificate_id: str,
) -> Optional[dict[str, Any]]:
    """Load the CertificateRegenerated event + tenant slug + chain tip
    for QR payload assembly. Mongo mirror of `c_cert._load_cert_context`.
    """
    safe_db = wrap(store)
    async with use_tenant(tenant_id):
        # Find the tenant slug.
        tenant_row = await safe_db.system("tenants").find_one(
            {"tenant_id": tenant_id}
        )
        # Find the CertificateRegenerated event by cert_id.
        cert_row = None
        async for r in safe_db.events.find(
            {"type": "CertificateRegenerated"}
        ).sort("seq", -1):
            payload = r.get("payload") or {}
            if payload.get("certificate_id") == certificate_id:
                cert_row = r
                break
        tip_row = await safe_db.events.find_one({}, sort=[("seq", -1)])
    if cert_row is None or tenant_row is None:
        return None
    payload = cert_row.get("payload") or {}
    cert = payload.get("certificate") or {}
    return {
        "tenant": tenant_row["slug"],
        "cert_id": certificate_id,
        "issued_at": cert.get("issued_at") or payload.get("effective_date"),
        "chain_tip": tip_row["event_hash"] if tip_row else None,
    }
