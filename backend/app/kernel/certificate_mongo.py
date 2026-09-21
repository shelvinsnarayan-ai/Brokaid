"""Phase R5a-2a — Mongo mirror of certificate regeneration.

R1 #3 (Build Brief v2.1 §8 item 3) extends the payload to carry a
client-captured `holder` and `requirements` block so the /verify page
can render exactly what was on the certificate PDF.

Same event authorship (CertificateRegenerated + VerifierTokenIssued).
Same token derivation (sha256 hex over the raw 32-byte URL-safe secret).
Same verifier_tokens shape plus optional `holder` and `requirements`.
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


def _clean_holder(h: dict | None) -> dict:
    if not isinstance(h, dict):
        return {}
    return {
        "name": (h.get("name") or "").strip()[:200],
        "address": (h.get("address") or "").strip()[:400],
        "email": (h.get("email") or "").strip()[:200],
    }


def _clean_requirements(r: dict | None) -> dict:
    if not isinstance(r, dict):
        return {}
    it = (r.get("interest_type") or "").strip().lower()
    if it not in ("additional_insured", "interested_party", "loss_payee", ""):
        it = ""
    # Accept both `special_requirements` (canonical) and `special` (shorthand
    # per Build Brief §7A). Persist under the canonical key and mirror under
    # `special` on read so either wire-name roundtrips verbatim.
    special = (
        r.get("special_requirements")
        or r.get("special")
        or ""
    )
    return {
        "interest_type": it,
        "effective_from": (r.get("effective_from") or "").strip()[:40],
        "effective_to": (r.get("effective_to") or "").strip()[:40],
        "special_requirements": special.strip()[:2000],
        "special": special.strip()[:2000],
    }


async def regenerate_certificate(
    *, store, tenant_id: str, policy_id: str,
    envelope_id: str | None = None,
    holder: dict | None = None,
    requirements: dict | None = None,
) -> dict[str, Any]:
    policy = await _load_policy(store, tenant_id, policy_id)
    if policy is None:
        raise LookupError(f"policy {policy_id} not found")
    default_holder = await _load_client_display(store, tenant_id, policy["client_id"])
    holder_block = _clean_holder(holder)
    if not holder_block.get("name"):
        holder_block["name"] = default_holder
    req_block = _clean_requirements(requirements)

    certificate_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    raw_token = secrets.token_urlsafe(32)
    hashed = hash_token(raw_token)
    expires_at = now + TOKEN_TTL

    safe_db = wrap(store)
    async with use_tenant(tenant_id):
        try:
            await safe_db.verifier_tokens.insert_one({
                "token_hash": hashed, "tenant_id": tenant_id,
                "policy_id": policy_id, "certificate_id": certificate_id,
                "issued_at": now, "expires_at": expires_at,
                "revoked_at": None, "rate_limit_count": 0,
                "holder": holder_block, "requirements": req_block,
            })
        except Exception:
            pass  # duplicate → idempotent

    certificate = {
        "certificate_id": certificate_id,
        "policy_id": policy_id,
        "holder_display": holder_block["name"] or default_holder,
        "holder": holder_block,
        "requirements": req_block,
        "insurer_display": policy["insurer_display"],
        "product": policy["product"],
        "cover_types": policy["schedule"].get("cover_types") or [],
        "excess": policy["schedule"].get("excess"),
        "territorial_limits": policy["schedule"].get("territorial_limits"),
        "effective_from": req_block.get("effective_from") or policy["inception"],
        "effective_to": req_block.get("effective_to") or policy["expiry"],
        "issued_at": now.isoformat(timespec="microseconds"),
        "mocked_insurer": True,
    }
    async with use_tenant(tenant_id):
        cert_event = await append_via_dispatch(
            store, tenant_id, "CertificateRegenerated",
            actor={"kind": "system", "id": "certificate", "name": "certificate"},
            payload={
                "envelope_id": envelope_id,
                "policy_id": policy_id,
                "certificate_id": certificate_id,
                "qr_token_hash": hashed,
                "effective_date": now.isoformat(timespec="microseconds"),
                "certificate": certificate,
                "holder": holder_block,
                "requirements": req_block,
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
        "ledger_event_id": cert_event.get("event_id") if isinstance(cert_event, dict) else None,
    }


async def revoke_token(store, tenant_id: str, token_hash: str) -> bool:
    safe_db = wrap(store)
    async with use_tenant(tenant_id):
        r = await safe_db.verifier_tokens.update_one(
            {"token_hash": token_hash, "revoked_at": None},
            {"$set": {"revoked_at": datetime.now(timezone.utc)}},
        )
    return r.modified_count == 1


# ---- Route-side helpers ---------------------------------------------
async def load_cert_context(
    store, tenant_id: str, certificate_id: str,
) -> Optional[dict[str, Any]]:
    safe_db = wrap(store)
    async with use_tenant(tenant_id):
        tenant_row = await safe_db.system("tenants").find_one(
            {"tenant_id": tenant_id}
        )
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
        "certificate": cert,
        "event_id": cert_row.get("event_id"),
    }


async def load_cert_by_token_hash(
    store, tenant_id: str, token_hash: str,
) -> Optional[dict[str, Any]]:
    """Return the verifier_tokens row for a given hash under the tenant ctx."""
    safe_db = wrap(store)
    async with use_tenant(tenant_id):
        row = await safe_db.verifier_tokens.find_one({"token_hash": token_hash})
    return row
