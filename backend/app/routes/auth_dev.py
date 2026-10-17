"""/*! brokaid:dev-only */
Dev-only impersonation + one-shot admin bundle download (R5c-4b — Mongo-only).

Mounted only when `AUTH_DEV_MODE=true` and refuses to mount if
`ENVIRONMENT=production`. Picks a seeded user by email and hands
the browser a session cookie with MFA already flagged as verified
so tests and manual walk-throughs can skip the full login flow.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr

from app.auth import sessions as sess
from app.auth.dispatch import auth_store, create_session_scoped
from app.kernel import devmode, identity
from app.kernel.rbac import LOGIN_CAPABLE_ROLES
from app.kernel.trail_dispatch import append_via_dispatch

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
    store = auth_store(request)
    user, tenant_hint = await identity.find_user_by_email_across_tenants(
        store, body.email,
    )
    if user is None or not devmode.dev_mode():
        raise HTTPException(status_code=404, detail="user_not_found")

    if user.kind == "operator":
        roles = await identity.list_brokerage_roles_across_tenants(
            store, user.user_id,
        )
        active = next(
            (r for r in roles if r.active and r.role in LOGIN_CAPABLE_ROLES),
            None,
        )
        if any(r.role == "platform_admin" for r in roles):
            raise HTTPException(status_code=404, detail="user_not_found")  # SEC-001b: never impersonate a platform admin
        if active is None:
            raise HTTPException(status_code=403, detail="no_login_capable_role")
        await devmode.require_dev_tenant(store, active.tenant_id, active.role)  # SEC-001b
        session = await create_session_scoped(
            store, active.tenant_id,
            user_id=user.user_id, brokerage_id=active.brokerage_id,
            active_role=active.role, mfa_verified=True,
        )
    else:
        links = await identity.list_client_links_across_tenants(
            store, user.user_id,
        )
        if not links:
            raise HTTPException(status_code=403, detail="no_client_link")
        first = links[0]
        await devmode.require_dev_tenant(store, first.tenant_id, first.role)  # SEC-001b
        session = await create_session_scoped(
            store, first.tenant_id,
            user_id=user.user_id, client_id=first.client_id,
            active_role=first.role, mfa_verified=True,
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
        "user_kind": user.kind,
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
    return the raw token."""
    from app.settings import get_settings

    settings = get_settings()
    if devmode.is_production():
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
        "ADMIN_BUNDLE_ARMED file=%s expires_at=%s ttl_min=%d token_sha256=%s",
        path.name,
        expires_at.isoformat(timespec="seconds"),
        ttl_minutes,
        token_sha256.hex()[:12],  # the raw token is returned to the caller, never logged
    )
    return raw


BROKAID_OPS_TENANT_SLUG = "brokaid-ops"
BROKAID_OPS_TENANT_NAME = "brokAId ops (build/audit)"


async def _get_or_create_ops_tenant_mongo(store) -> str:
    """Return the tenant_id for the `brokaid-ops` operational tenant,
    creating its genesis row + TenantGenesis ledger event on first use.
    """
    from app.db.tenant_safe import wrap
    safe = wrap(store)
    row = await safe.system("tenants").find_one({"slug": BROKAID_OPS_TENANT_SLUG})
    if row is not None:
        return row["tenant_id"]
    from app.kernel import trail_mongo
    from app.kernel.tenant_ctx import use_tenant
    new_tid = str(uuid.uuid4())
    async with use_tenant(new_tid):
        t, _ = await trail_mongo.create_tenant(
            store, BROKAID_OPS_TENANT_SLUG, BROKAID_OPS_TENANT_NAME,
            tenant_id_override=new_tid,
        )
    return t.tenant_id


async def _emit_bundle_event(
    request: Request, token_sha256_hex: str, ip: str, ok: bool
) -> None:
    """Fire-and-forget: emit an AdminBundleDownloadRequested ledger event."""
    store = getattr(request.app.state, "mongo_db", None)
    if store is None:
        _log.warning("bundle event: no mongo_db on app.state; skipping ledger")
        return
    try:
        tenant_id = await _get_or_create_ops_tenant_mongo(store)
        await append_via_dispatch(
            store, tenant_id, "AdminBundleDownloadRequested",
            actor={"kind": "system", "id": "admin-bundle", "name": "admin-bundle"},
            payload={"token_hash": token_sha256_hex, "ip": ip, "ok": ok},
            idempotency_key=f"admin-bundle:{uuid.uuid4()}",
        )
    except Exception as e:  # pragma: no cover - ledger must never break the route
        _log.warning("bundle event: emission failed: %r", e)


@admin_bundle_router.get("/bundle")
async def download_admin_bundle(
    request: Request, token: str = Query(..., min_length=8, max_length=256)
) -> Response:
    """Single-use, expiring, ledger-audited bundle download."""
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
        request.app.state.admin_bundle = None
        await _emit_bundle_event(request, incoming_hex, ip, ok=False)
        raise _gone("admin_bundle_expired")

    if not hmac.compare_digest(incoming_sha256, state.token_sha256):
        await _emit_bundle_event(request, incoming_hex, ip, ok=False)
        raise _gone("admin_bundle_bad_token")

    state.consumed = True
    path = state.path
    filename = state.filename
    await _emit_bundle_event(request, incoming_hex, ip, ok=True)
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
