"""Public verifier route — unauthenticated (R1 #3 — Build Brief v2.1 §8).

Verdict-first API (backward compatible with the pre-existing 4xx codes
so the old VerifyPublic front-end keeps working):

    HTTP 200 · {"verdict": "VALID"|"TAMPERED", ...cert}
    HTTP 404 · {"verdict": "NOT_FOUND"}
    HTTP 410 · {"verdict": "REVOKED"|"EXPIRED"}
    HTTP 429 · {"verdict": "RATE_LIMITED"}

TAMPERED is triggered by a `tampered_flag: true` marker on the
verifier_tokens row (used by the SIRC demo tokens). Real tampering
detection (payload signature check) will layer on top of this once
CarrierChannel signs the certificate blob — same verdict wire format.

Emits `VerifierRead` events into the tenant ledger.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.db.tenant_safe import wrap as _wrap_safe
from app.kernel.certificate import hash_token
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch


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
        "tampered_flag": bool(row.get("tampered_flag")),
        "holder": row.get("holder") or {},
        "requirements": row.get("requirements") or {},
    }


async def _load_cert_event(store, tenant_id: str, certificate_id: str,
                           hashed: str) -> dict | None:
    safe = _wrap_safe(store)
    payload = None
    event_id = None
    async with use_tenant(tenant_id):
        async for r in safe.events.find(
            {"type": "CertificateRegenerated"}
        ).sort("seq", -1):
            p = r.get("payload") or {}
            if p.get("certificate_id") == certificate_id:
                payload = p
                event_id = r.get("event_id")
                break
    await safe.system("verifier_tokens").update_one(
        {"token_hash": hashed},
        {"$inc": {"rate_limit_count": 1}},
    )
    if payload is None:
        return None
    return {"payload": payload, "event_id": event_id}


async def _emit_read(store, tenant_id: str, *, certificate_id: str,
                     verdict: str, hashed: str, ip: str) -> None:
    try:
        async with use_tenant(tenant_id):
            await append_via_dispatch(
                store, tenant_id, "VerifierRead",
                actor={"kind": "system", "id": "verifier", "name": "verifier"},
                payload={
                    "certificate_id": certificate_id,
                    "token_hash": hashed[:16],  # partial for audit, no full leak
                    "verdict": verdict,
                    "verifier_ip": ip[:64],
                    "read_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                },
            )
    except Exception:
        pass  # audit is best-effort; never fail a public read


@router.get("/{token}")
async def verify(token: str, request: Request) -> dict[str, Any]:
    ip = request.client.host if request.client else "unknown"
    if _rate_limited(ip):
        raise HTTPException(status_code=429, detail={"verdict": "RATE_LIMITED"})

    if not token or len(token) < 20 or len(token) > 200:
        raise HTTPException(status_code=404, detail={"verdict": "NOT_FOUND"})

    hashed = hash_token(token)
    store = _store(request)

    row = await _lookup_token(store, hashed)
    if row is None:
        raise HTTPException(status_code=404, detail={"verdict": "NOT_FOUND"})

    tenant_id = row["tenant_id"]
    certificate_id = row["certificate_id"]
    policy_id = row["policy_id"]

    if row["revoked_at"] is not None:
        await _emit_read(store, tenant_id, certificate_id=certificate_id,
                         verdict="REVOKED", hashed=hashed, ip=ip)
        raise HTTPException(status_code=410, detail={"verdict": "REVOKED"})

    exp = row["expires_at"]
    if hasattr(exp, "tzinfo") and exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if exp < now:
        await _emit_read(store, tenant_id, certificate_id=certificate_id,
                         verdict="EXPIRED", hashed=hashed, ip=ip)
        raise HTTPException(status_code=410, detail={"verdict": "EXPIRED"})

    cert_event = await _load_cert_event(store, tenant_id, certificate_id, hashed)
    if cert_event is None:
        raise HTTPException(status_code=404, detail={"verdict": "NOT_FOUND"})
    payload = cert_event["payload"]
    event_id = cert_event["event_id"]
    cert = payload.get("certificate") or {}

    verdict = "TAMPERED" if row["tampered_flag"] else "VALID"
    await _emit_read(store, tenant_id, certificate_id=certificate_id,
                     verdict=verdict, hashed=hashed, ip=ip)

    return {
        "verdict": verdict,
        "holder_display": cert.get("holder_display") or (row["holder"].get("name") or ""),
        "holder": cert.get("holder") or row["holder"],
        "requirements": cert.get("requirements") or row["requirements"],
        "insurer_display": cert.get("insurer_display"),
        "product": cert.get("product"),
        "cover_types": cert.get("cover_types") or [],
        "excess": cert.get("excess"),
        "territorial_limits": cert.get("territorial_limits"),
        "effective_from": cert.get("effective_from"),
        "effective_to": cert.get("effective_to"),
        "issued_at": cert.get("issued_at"),
        "policy_id": policy_id,
        "certificate_id": certificate_id,
        "ledger_event_id": event_id,
        "mocked_insurer": True,
    }
