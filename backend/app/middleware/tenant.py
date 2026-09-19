"""brokAId Phase R3 — TenantContextMiddleware (R5c-4b — Mongo-only).

Attaches a tenant_id to a `ContextVar` for the duration of each
request based on the current session cookie. Every downstream
Mongo access via `TenantSafeDatabase` reads that var and fails
loud (`NoTenantContextError`) if it's unset.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.auth import sessions as sess
from app.kernel.tenant_ctx import (
    reset_tenant_unsafe, set_tenant_unsafe,
)


TENANT_EXEMPT_PREFIXES: tuple[str, ...] = (
    "/api/healthz",
    "/api/openapi.json",
    "/api/auth/",
    "/api/verifier/",
    "/docs",
    "/redoc",
)


class TenantContextMiddleware(BaseHTTPMiddleware):
    """Bind current_tenant_id from the session cookie for each request."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        token = None
        try:
            token = await self._bind_tenant_from_session(request)
        except Exception:  # noqa: BLE001
            token = None
        try:
            return await call_next(request)
        finally:
            if token is not None:
                reset_tenant_unsafe(token)

    async def _bind_tenant_from_session(self, request: Request):
        cookie = request.cookies.get(sess.COOKIE_NAME)
        if not cookie:
            return None
        # Prefer app-attached mongo_db (set at lifespan); fall back to
        # module-level client for out-of-app cases (workers/tests).
        store = getattr(request.app.state, "mongo_db", None)
        if store is None:
            from app.db import mongo as mongo_mod
            store = mongo_mod.get_db_sync()
        if store is None:
            return None
        from app.kernel import identity
        try:
            session = await identity.read_session(store, cookie)
        except Exception:
            session = None
        if session is None:
            return None
        request.state.session = session
        return set_tenant_unsafe(session.tenant_id)


__all__ = ["TenantContextMiddleware", "TENANT_EXEMPT_PREFIXES"]
