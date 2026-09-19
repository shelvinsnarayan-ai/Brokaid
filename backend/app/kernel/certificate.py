"""Certificate regeneration + tokenised verifier issuance.

The verifier token is a 32-byte URL-safe random string. Only its
SHA-256 hex hash is stored in `verifier_tokens`. The raw token is
returned to the caller ONCE (the scene page bakes it into a QR).
Expiry: 90 days.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any


from app.kernel.trail import append


TOKEN_TTL = timedelta(days=90)


def _token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _load_policy(
    pool: object, tenant_id: str, policy_id: str,
) -> dict[str, Any] | None:
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            row = await c.fetchrow(
                "SELECT policy_id::text, client_id::text, product, schedule, "
                "       premium_amount::text, premium_currency, status, "
                "       insurer_display, inception, expiry "
                "  FROM policies "
                " WHERE tenant_id=$1::uuid AND policy_id=$2::uuid",
                tenant_id, policy_id,
            )
    if not row:
        return None
    return {
        "policy_id": row["policy_id"],
        "client_id": row["client_id"],
        "product": row["product"],
        "schedule": (
            json.loads(row["schedule"])
            if isinstance(row["schedule"], str)
            else dict(row["schedule"])
        ),
        "premium_amount": row["premium_amount"],
        "premium_currency": row["premium_currency"],
        "status": row["status"],
        "insurer_display": row["insurer_display"],
        "inception": row["inception"].isoformat(timespec="microseconds"),
        "expiry": row["expiry"].isoformat(timespec="microseconds"),
    }


async def _load_client_display(
    pool: object, tenant_id: str, client_id: str,
) -> str:
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            row = await c.fetchrow(
                "SELECT display_name FROM clients "
                "WHERE tenant_id=$1::uuid AND client_id=$2::uuid",
                tenant_id, client_id,
            )
    return row["display_name"] if row else ""


async def regenerate_certificate(
    *,
    pool: object,
    tenant_id: str,
    policy_id: str,
    envelope_id: str | None = None,
) -> dict[str, Any]:
    policy = await _load_policy(pool, tenant_id, policy_id)
    if policy is None:
        raise LookupError(f"policy {policy_id} not found")
    holder = await _load_client_display(pool, tenant_id, policy["client_id"])

    certificate_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    raw_token = secrets.token_urlsafe(32)
    hashed = _token_hash(raw_token)
    expires_at = now + TOKEN_TTL

    # Store token hash only.
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            await c.execute(
                """
                INSERT INTO verifier_tokens
                  (token_hash, tenant_id, policy_id, certificate_id,
                   issued_at, expires_at)
                VALUES ($1, $2::uuid, $3::uuid, $4::uuid, $5, $6)
                """,
                hashed, tenant_id, policy_id, certificate_id, now, expires_at,
            )

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

    await append(
        pool, tenant_id, "CertificateRegenerated",
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
        mocked=True,
        idempotency_key=f"cert:regen:{certificate_id}",
    )
    await append(
        pool, tenant_id, "VerifierTokenIssued",
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
        "raw_token": raw_token,          # ONLY returned here — never stored.
        "token_hash": hashed,
        "expires_at": expires_at.isoformat(timespec="microseconds"),
    }


async def revoke_token(pool: object, tenant_id: str, token_hash: str) -> bool:
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            res = await c.execute(
                "UPDATE verifier_tokens SET revoked_at=NOW() "
                "WHERE tenant_id=$1::uuid AND token_hash=$2 AND revoked_at IS NULL",
                tenant_id, token_hash,
            )
    return res.endswith("1")


def hash_token(raw: str) -> str:
    return _token_hash(raw)


# ---- R5b-1a dispatch wrapper ------------------------------------------
def _is_mongo_store(store) -> bool:
    """Detect Motor DB / TenantSafeDatabase without importing at
    module load (avoids circular imports on cold routes)."""
    from motor.motor_asyncio import AsyncIOMotorDatabase
    from app.db.tenant_safe import TenantSafeDatabase
    return isinstance(store, (AsyncIOMotorDatabase, TenantSafeDatabase))


async def regenerate_certificate_dispatch(
    *,
    store,
    tenant_id: str,
    policy_id: str,
    envelope_id: str | None = None,
) -> dict[str, Any]:
    """Byte-identical regeneration path across backends.

    Under Mongo: delegates to `certificate_mongo.regenerate_certificate`
    which lives on top of `trail_dispatch.append_via_dispatch`.
    Under PG: calls the local `regenerate_certificate` which uses
    `trail.append` directly.

    `certificate_id` and `raw_token` are non-deterministic by design
    (uuid4 + secrets.token_urlsafe) so hashes differ across calls —
    that's expected. The R2 byte-identical guarantee applies to
    identical inputs, which for cert regen means the same envelope
    is exercised only once per store per scene.
    """
    if _is_mongo_store(store):
        from app.kernel import certificate_mongo
        return await certificate_mongo.regenerate_certificate(
            store=store, tenant_id=tenant_id,
            policy_id=policy_id, envelope_id=envelope_id,
        )
    return await regenerate_certificate(
        pool=store, tenant_id=tenant_id,
        policy_id=policy_id, envelope_id=envelope_id,
    )
