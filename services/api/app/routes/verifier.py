"""Public verifier route — unauthenticated.

Rate limit: 20 requests/hour per IP. In-memory sliding window
(`SIMPLE_INMEMORY_RATE_LIMIT`). Swap to Redis in phase 16 if needed.

Reads projections only. The one mutation is a counter increment on
successful lookups, deliberately outside RLS because the verifier is
unauthenticated (tenant resolves from the token row).
"""
from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.kernel.certificate import hash_token


router = APIRouter(prefix="/api/verifier")


# ---- In-memory rate limiter (per-IP sliding window) ------------------------

_WINDOW_SECONDS = 3600
_MAX_REQUESTS = 20
_hits: dict[str, deque[float]] = defaultdict(deque)


def _rate_limited(ip: str) -> bool:
    now = time.time()
    q = _hits[ip]
    while q and (now - q[0]) > _WINDOW_SECONDS:
        q.popleft()
    if len(q) >= _MAX_REQUESTS:
        return True
    q.append(now)
    return False


def _reset_rate_limiter() -> None:
    _hits.clear()


@router.get("/{token}")
async def verify(token: str, request: Request) -> dict[str, Any]:
    ip = request.client.host if request.client else "unknown"
    if _rate_limited(ip):
        raise HTTPException(status_code=429, detail="rate_limited")

    if not token or len(token) < 20 or len(token) > 200:
        raise HTTPException(status_code=404, detail="not_found")

    hashed = hash_token(token)
    pool = request.app.state.pool
    # Lookup on PK first (no tenant context — that's the design).
    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT tenant_id::text AS tenant_id, policy_id::text AS policy_id, "
            "       certificate_id::text AS certificate_id, "
            "       revoked_at, expires_at "
            "  FROM verifier_tokens WHERE token_hash=$1",
            hashed,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="not_found")
    if row["revoked_at"] is not None:
        raise HTTPException(status_code=410, detail="revoked")
    if row["expires_at"] < row["expires_at"].__class__.now(row["expires_at"].tzinfo):
        raise HTTPException(status_code=410, detail="expired")

    tenant_id = row["tenant_id"]
    policy_id = row["policy_id"]
    certificate_id = row["certificate_id"]

    # Now scope to tenant and pull the certificate payload from the
    # CertificateRegenerated event (source of truth).
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            ev = await c.fetchrow(
                "SELECT payload FROM events "
                " WHERE tenant_id=$1::uuid AND type='CertificateRegenerated' "
                "   AND payload->>'certificate_id' = $2 "
                " ORDER BY seq DESC LIMIT 1",
                tenant_id, certificate_id,
            )
            await c.execute(
                "UPDATE verifier_tokens SET rate_limit_count=rate_limit_count+1 "
                "WHERE token_hash=$1",
                hashed,
            )
    if ev is None:
        raise HTTPException(status_code=404, detail="certificate_missing")
    payload = json.loads(ev["payload"]) if isinstance(ev["payload"], str) else ev["payload"]
    cert = payload.get("certificate") or {}

    # Public-safe subset only. No tenant_id, no model_id, no PII beyond
    # the client display name (which is the certificate holder by design).
    return {
        "holder_display": cert.get("holder_display"),
        "insurer_display": cert.get("insurer_display"),
        "product": cert.get("product"),
        "cover_types": cert.get("cover_types") or [],
        "excess": cert.get("excess"),
        "territorial_limits": cert.get("territorial_limits"),
        "effective_from": cert.get("effective_from"),
        "effective_to": cert.get("effective_to"),
        "issued_at": cert.get("issued_at"),
        "policy_id": policy_id,
        "mocked_insurer": True,
    }
