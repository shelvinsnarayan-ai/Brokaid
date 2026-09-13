"""/*! brokaid:dev-only */
Dev-only impersonation + one-shot admin bundle download.
Mounted only when `AUTH_DEV_MODE=true` and refuses to mount if
`ENVIRONMENT=production` (belt-and-braces alongside
`Settings.assert_safe_to_start`).

Picks a seeded user by email and hands the browser a session cookie
with MFA already flagged as verified so tests and manual walk-throughs
can skip the full login flow.

The `/api/_admin/bundle` route hands the current off-box git bundle
to a single caller holding a random token, then burns the token.
See `mount_admin_bundle_route` below.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr

from app.auth import sessions as sess
from app.kernel.rbac import LOGIN_CAPABLE_ROLES
from app.kernel.trail import append as trail_append

router = APIRouter(prefix="/api/auth/dev")
admin_bundle_router = APIRouter(prefix="/api/_admin")

_log = logging.getLogger("brokaid.api.admin_bundle")


class ImpersonateRequest(BaseModel):
    email: EmailStr


@router.post("/impersonate")
async def impersonate(
    body: ImpersonateRequest, request: Request, response: Response
) -> dict:
    """Dev impersonation. Body: {email}. Returns session context."""
    pool = request.app.state.pool
    async with pool.acquire() as c:
        user = await c.fetchrow(
            "SELECT user_id, kind FROM users WHERE email = $1", body.email
        )
    if user is None:
        raise HTTPException(status_code=404, detail="user_not_found")

    if user["kind"] == "operator":
        async with pool.acquire() as c:
            rows = await c.fetch(
                """
                SELECT ubr.role, b.brokerage_id::text AS brokerage_id,
                       b.tenant_id::text AS tenant_id
                  FROM user_brokerage_roles ubr
                  JOIN brokerages b ON b.brokerage_id = ubr.brokerage_id
                 WHERE ubr.user_id = $1::uuid AND ubr.active = TRUE
                """,
                str(user["user_id"]),
            )
        row = next(
            (r for r in rows if r["role"] in LOGIN_CAPABLE_ROLES), None
        )
        if row is None:
            raise HTTPException(status_code=403, detail="no_login_capable_role")
        session = await sess.create(
            pool,
            user_id=str(user["user_id"]),
            tenant_id=row["tenant_id"],
            brokerage_id=row["brokerage_id"],
            active_role=row["role"],
            mfa_verified=True,   # dev-only bypass
        )
    else:
        async with pool.acquire() as c:
            row = await c.fetchrow(
                """
                SELECT tenant_id::text AS tenant_id, client_id::text AS client_id, role
                  FROM user_client_links WHERE user_id = $1::uuid LIMIT 1
                """,
                str(user["user_id"]),
            )
        if row is None:
            raise HTTPException(status_code=403, detail="no_client_link")
        session = await sess.create(
            pool,
            user_id=str(user["user_id"]),
            tenant_id=row["tenant_id"],
            client_id=row["client_id"],
            active_role=row["role"],
            mfa_verified=True,
        )

    response.set_cookie(
        key=sess.COOKIE_NAME, value=session.session_id,
        httponly=True, samesite="lax", secure=False, path="/",
        max_age=12 * 3600,
    )
    return {
        "session_id": session.session_id,
        "tenant_id": session.tenant_id,
        "active_role": session.active_role,
        "user_kind": user["kind"],
    }


# ---------------------------------------------------------------------------
# One-shot admin bundle download (dev-only)
# ---------------------------------------------------------------------------


class _AdminBundleState:
    """State kept on `app.state.admin_bundle`. In-memory only."""

    __slots__ = ("token_sha256", "expires_at", "consumed", "path", "filename")

    def __init__(
        self, token_sha256: bytes, expires_at: datetime, path: Path
    ) -> None:
        self.token_sha256 = token_sha256
        self.expires_at = expires_at
        self.consumed = False
        self.path = path
        self.filename = path.name


def mount_admin_bundle_route(
    app: FastAPI, *, bundle_path: str, ttl_minutes: int = 30
) -> str:
    """Generate a fresh single-use token, attach state to the app, and
    return the raw token.  The caller (main.py) logs a full URL.

    Refuses to arm in production.  Refuses to arm if the bundle file
    does not exist.  Idempotent per app instance: if state is already
    attached, returns "" and does not rotate the token.
    """
    # Belt-and-braces: refuse to arm in a production build. Settings
    # already refuse to boot the API at all in this state, but be
    # explicit here too.
    from app.settings import get_settings

    settings = get_settings()
    if settings.environment == "production":
        raise RuntimeError(
            "admin bundle download route refuses to mount in production"
        )
    if not settings.auth_dev_mode:
        raise RuntimeError(
            "admin bundle download route requires AUTH_DEV_MODE=true"
        )

    if getattr(app.state, "admin_bundle", None) is not None:
        return ""

    path = Path(bundle_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"admin bundle not readable: {path}")

    raw = secrets.token_urlsafe(32)
    token_sha256 = hashlib.sha256(raw.encode("ascii")).digest()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)

    app.state.admin_bundle = _AdminBundleState(
        token_sha256=token_sha256,
        expires_at=expires_at,
        path=path,
    )
    _log.info(
        "ADMIN_BUNDLE_ARMED file=%s expires_at=%s ttl_min=%d token=%s",
        path.name,
        expires_at.isoformat(timespec="seconds"),
        ttl_minutes,
        raw,
    )
    return raw


BROKAID_OPS_TENANT_SLUG = "brokaid-ops"
BROKAID_OPS_TENANT_NAME = "brokAId ops (build/audit)"


async def _get_or_create_ops_tenant(pool) -> str:
    """Return the tenant_id for the `brokaid-ops` operational tenant,
    creating its genesis row + TenantGenesis ledger event on first use.
    All admin/system events (bundle downloads, migration reconciles,
    etc.) land under this tenant, isolated from customer tenants by RLS.
    """
    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT tenant_id::text AS tenant_id FROM tenants WHERE slug=$1",
            BROKAID_OPS_TENANT_SLUG,
        )
    if row is not None:
        return row["tenant_id"]
    from app.kernel.trail import create_tenant

    tenant, _ = await create_tenant(
        pool, BROKAID_OPS_TENANT_SLUG, BROKAID_OPS_TENANT_NAME
    )
    return tenant.tenant_id


async def _emit_bundle_event(
    request: Request, token_sha256_hex: str, ip: str, ok: bool
) -> None:
    """Fire-and-forget: emit an AdminBundleDownloadRequested ledger
    event under the `brokaid-ops` tenant. Missing/failed emission logs
    a warning and does not fail the response.
    """
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        _log.warning("bundle event: no pool on app.state; skipping ledger")
        return
    try:
        tenant_id = await _get_or_create_ops_tenant(pool)
        await trail_append(
            pool,
            tenant_id,
            "AdminBundleDownloadRequested",
            actor={"kind": "system", "id": "admin-bundle", "name": "admin-bundle"},
            payload={
                "token_hash": token_sha256_hex,
                "ip": ip,
                "ok": ok,
            },
            idempotency_key=f"admin-bundle:{uuid.uuid4()}",
        )
    except Exception as e:  # pragma: no cover - ledger must never break the route
        _log.warning("bundle event: emission failed: %r", e)


@admin_bundle_router.get("/bundle")
async def download_admin_bundle(
    request: Request, token: str = Query(..., min_length=8, max_length=256)
) -> Response:
    """Single-use, expiring, ledger-audited bundle download.

    - Wrong / already-consumed / expired token → 410 Gone.
    - Correct token → stream the file once, mark consumed.
    Emits `AdminBundleDownloadRequested` on every hit (200 or 410).
    """
    state: _AdminBundleState | None = getattr(
        request.app.state, "admin_bundle", None
    )
    ip = request.client.host if request.client else "unknown"
    incoming_sha256 = hashlib.sha256(token.encode("ascii")).digest()
    incoming_hex = incoming_sha256.hex()

    def _gone(reason: str) -> HTTPException:
        return HTTPException(status_code=410, detail=reason)

    if state is None:
        await _emit_bundle_event(request, incoming_hex, ip, ok=False)
        raise _gone("admin_bundle_not_armed")

    now = datetime.now(timezone.utc)
    if state.consumed:
        await _emit_bundle_event(request, incoming_hex, ip, ok=False)
        raise _gone("admin_bundle_consumed")
    if now >= state.expires_at:
        # Expiry: drop the state so a second hit doesn't leak the hash.
        request.app.state.admin_bundle = None
        await _emit_bundle_event(request, incoming_hex, ip, ok=False)
        raise _gone("admin_bundle_expired")

    if not hmac.compare_digest(incoming_sha256, state.token_sha256):
        await _emit_bundle_event(request, incoming_hex, ip, ok=False)
        raise _gone("admin_bundle_bad_token")

    # Burn the token BEFORE streaming so racing requests get 410.
    state.consumed = True
    path = state.path
    filename = state.filename
    # Emit the successful-request ledger event before returning.
    await _emit_bundle_event(request, incoming_hex, ip, ok=True)
    # After serving once, clear the state to leave no record around.
    request.app.state.admin_bundle = None

    return FileResponse(
        path=str(path),
        media_type="application/octet-stream",
        filename=filename,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
