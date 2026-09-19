"""Public verifier route — unauthenticated (R5c-4b — Mongo-only)."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.db.tenant_safe import wrap as _wrap_safe
from app.kernel.certificate import hash_token
from app.kernel.tenant_ctx import use_tenant


router = APIRouter(prefix="/api/verifier")

_WINDOW_SECONDS = 3600
_MAX_REQUESTS = 20
_hits: dict[str, deque[float]] = defaultdict(deque)


def _store(request: Request):
    return request.app.state.mongo_db


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


async def _lookup_token(store, hashed: str) -> dict | None:
    safe = _wrap_safe(store)
    row = await safe.system("verifier_tokens").find_one({"token_hash": hashed})
    if row is None:
        return None
    return {
        "tenant_id": row["tenant_id"], "policy_id": row["policy_id"],
        "certificate_id": row["certificate_id"],
        "revoked_at": row.get("revoked_at"), "expires_at": row.get("expires_at"),
    }


async def _load_cert_event(store, tenant_id: str, certificate_id: str,
                           hashed: str) -> dict | None:
    safe = _wrap_safe(store)
    payload = None
    async with use_tenant(tenant_id):
        async for r in safe.events.find(
            {"type": "CertificateRegenerated"}
        ).sort("seq", -1):
            p = r.get("payload") or {}
            if p.get("certificate_id") == certificate_id:
                payload = p
                break
    await safe.system("verifier_tokens").update_one(
        {"token_hash": hashed},
        {"$inc": {"rate_limit_count": 1}},
    )
    return payload


@router.get("/{token}")
async def verify(token: str, request: Request) -> dict[str, Any]:
    ip = request.client.host if request.client else "unknown"
    if _rate_limited(ip):
        raise HTTPException(status_code=429, detail="rate_limited")

    if not token or len(token) < 20 or len(token) > 200:
        raise HTTPException(status_code=404, detail="not_found")

    hashed = hash_token(token)
    store = _store(request)

    row = await _lookup_token(store, hashed)
    if row is None:
        raise HTTPException(status_code=404, detail="not_found")
    if row["revoked_at"] is not None:
        raise HTTPException(status_code=410, detail="revoked")
    exp = row["expires_at"]
    if hasattr(exp, "tzinfo") and exp.tzinfo is None:
        from datetime import timezone as _tz
        exp = exp.replace(tzinfo=_tz.utc)
    now = datetime.now(exp.tzinfo) if hasattr(exp, "tzinfo") and exp.tzinfo \
        else datetime.now(timezone.utc)
    if exp < now:
        raise HTTPException(status_code=410, detail="expired")

    tenant_id = row["tenant_id"]
    policy_id = row["policy_id"]
    certificate_id = row["certificate_id"]

    payload = await _load_cert_event(store, tenant_id, certificate_id, hashed)
    if payload is None:
        raise HTTPException(status_code=404, detail="certificate_missing")
    cert = payload.get("certificate") or {}

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
