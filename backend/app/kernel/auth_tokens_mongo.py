"""Phase R4c — Mongo backends for the two bootstrap-primitive token
collections (verifier_tokens + magic_link_tokens).

# tenant-safe: system-only

Both collections are read pre-auth: the raw token is the tenant-
discovery vehicle, so lookup by `token_hash` MUST work without a
tenant context. Approved for `_SYSTEM_ALLOW` in R4c, same shape as
`sessions` in R4a. Every write, however, flows through the
tenant-safe wrapper under the appropriate `use_tenant(tid)`.

Verifier tokens
---------------
Created by `certificate.regenerate_certificate` under a session-
bound ctx. Read by the public `/api/verifier/{token}` route,
which has no ctx — it uses `safe_db.system("verifier_tokens")`
to look up by hash, then enters `use_tenant(row.tenant_id)` for
the downstream CertificateRegenerated event read.

Magic-link tokens
-----------------
Two-step:
  1. Send: `/api/auth/client/magic/send` requires `brokerage_slug`
     to resolve tenant BEFORE any user lookup. Under Mongo the
     Postgres global-email lookup would leak user existence across
     tenants — the slug is the fix.
  2. Validate: `/api/auth/client/magic/{token}` looks up by hash
     via `system("magic_link_tokens")`, resolves tenant, enters
     `use_tenant`, marks used_at.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

from app.db.tenant_safe import TenantSafeDatabase, wrap
from app.kernel.tenant_ctx import use_tenant


VERIFIER_TOKEN_TTL = timedelta(days=90)
MAGIC_LINK_TTL = timedelta(minutes=15)


def _sha256(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


_indexes_initialised: set[int] = set()


async def _ensure_indexes(safe_db: TenantSafeDatabase) -> None:
    # tenant-safe: system-only
    raw = safe_db.raw
    key = id(raw)
    if key in _indexes_initialised:
        return
    await raw["verifier_tokens"].create_index(
        [("token_hash", ASCENDING)], unique=True, name="vt_token_hash_uniq",
    )
    await raw["verifier_tokens"].create_index(
        [("tenant_id", ASCENDING), ("policy_id", ASCENDING)],
        name="vt_tenant_policy",
    )
    await raw["magic_link_tokens"].create_index(
        [("token_hash", ASCENDING)], unique=True, name="mlt_token_hash_uniq",
    )
    await raw["magic_link_tokens"].create_index(
        [("user_id", ASCENDING)], name="mlt_user",
    )
    _indexes_initialised.add(key)


# ---- Verifier tokens -------------------------------------------------
async def issue_verifier_token(
    db, *, tenant_id: str, policy_id: str, certificate_id: str,
    ttl: timedelta = VERIFIER_TOKEN_TTL,
) -> tuple[str, str, datetime]:
    """Issue a fresh verifier token. Returns (raw_token, token_hash,
    expires_at). Writes go through the wrapper under
    `use_tenant(tenant_id)`."""
    safe_db = wrap(db)
    await _ensure_indexes(safe_db)
    raw = secrets.token_urlsafe(32)
    h = _sha256(raw)
    now = datetime.now(timezone.utc)
    expires = now + ttl
    async with use_tenant(tenant_id):
        try:
            await safe_db.verifier_tokens.insert_one({
                "token_hash": h, "tenant_id": tenant_id,
                "policy_id": policy_id,
                "certificate_id": certificate_id,
                "issued_at": now, "expires_at": expires,
                "revoked_at": None, "rate_limit_count": 0,
            })
        except DuplicateKeyError:
            pass
    return raw, h, expires


async def find_verifier_token_system(db, raw_token: str) -> Optional[dict]:
    """Pre-auth token lookup. Returns the raw row (dict) or None.

    # tenant-safe: system-only — the caller (public verifier route)
    has no ctx yet; the row's `tenant_id` IS the discovery result.
    """
    safe_db = wrap(db)
    await _ensure_indexes(safe_db)
    h = _sha256(raw_token)
    row = await safe_db.system("verifier_tokens").find_one({"token_hash": h})
    return row


async def increment_verifier_token_hit(
    db, token_hash: str, tenant_id: str,
) -> None:
    """Bump the rate-limit counter under the tenant ctx."""
    safe_db = wrap(db)
    async with use_tenant(tenant_id):
        await safe_db.verifier_tokens.update_one(
            {"token_hash": token_hash},
            {"$inc": {"rate_limit_count": 1}},
        )


async def revoke_verifier_token(db, *, tenant_id: str, token_hash: str) -> bool:
    safe_db = wrap(db)
    async with use_tenant(tenant_id):
        r = await safe_db.verifier_tokens.update_one(
            {"token_hash": token_hash, "revoked_at": None},
            {"$set": {"revoked_at": datetime.now(timezone.utc)}},
        )
    return r.modified_count == 1


def hash_verifier_token(raw: str) -> str:
    return _sha256(raw)


# ---- Magic-link tokens (two-step) ------------------------------------
async def issue_magic_link(
    db, *, user_id: str, tenant_id: str,
    ttl: timedelta = MAGIC_LINK_TTL,
) -> tuple[str, datetime]:
    """Step 1 write. Returns (raw_token, expires_at). Caller resolved
    tenant_id from a brokerage_slug BEFORE calling this."""
    safe_db = wrap(db)
    await _ensure_indexes(safe_db)
    raw = secrets.token_urlsafe(32)
    h = _sha256(raw)
    expires = datetime.now(timezone.utc) + ttl
    async with use_tenant(tenant_id):
        try:
            await safe_db.magic_link_tokens.insert_one({
                "token_hash": h, "tenant_id": tenant_id,
                "user_id": user_id,
                "expires_at": expires, "used_at": None,
                "created_at": datetime.now(timezone.utc),
            })
        except DuplicateKeyError:
            pass
    return raw, expires


async def consume_magic_link(
    db, raw_token: str,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Step 2 read+mark-used. Returns (user_id, tenant_id, error).

    Errors: 'unknown' | 'expired' | 'already_used'. On success both
    ids are returned and the row is atomically flipped to used.

    # tenant-safe: system-only — token IS the tenant-discovery vehicle.
    """
    safe_db = wrap(db)
    await _ensure_indexes(safe_db)
    h = _sha256(raw_token)
    row = await safe_db.system("magic_link_tokens").find_one({"token_hash": h})
    if row is None:
        return None, None, "unknown"
    if row.get("used_at") is not None:
        return None, None, "already_used"
    exp = row["expires_at"]
    now = datetime.now(timezone.utc)
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < now:
        return None, None, "expired"
    tenant_id = row["tenant_id"]
    async with use_tenant(tenant_id):
        r = await safe_db.magic_link_tokens.update_one(
            {"token_hash": h, "used_at": None},
            {"$set": {"used_at": now}},
        )
    if r.modified_count != 1:
        # Race: someone else consumed it between our read and update.
        return None, None, "already_used"
    return row["user_id"], tenant_id, None
