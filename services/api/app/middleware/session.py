"""Session middleware + tenant-scoped connection dependency."""
from __future__ import annotations

from typing import AsyncIterator

from fastapi import Depends, HTTPException, Request

from app.auth import sessions as sess


async def _load_session(request: Request):
    cookie = request.cookies.get(sess.COOKIE_NAME)
    if not cookie:
        return None
    pool = request.app.state.pool
    return await sess.load(pool, cookie)


async def session_context(request: Request):
    """FastAPI dependency: attaches request.state.session (may be None)."""
    if not hasattr(request.state, "session"):
        request.state.session = await _load_session(request)
    return request.state.session


async def require_session(request: Request):
    """Non-optional: 401 if no valid session."""
    session = await session_context(request)
    if session is None:
        raise HTTPException(status_code=401, detail="not_authenticated")
    return session


async def require_operator_session(request: Request):
    """Operator endpoints — MFA-verified session AND an operator-role
    active_role required. Client dev-impersonated sessions can be
    MFA-verified for demo simplicity, so we also gate on role kind."""
    from app.kernel.rbac import ALL_OPERATOR_ROLES

    session = await require_session(request)
    if not session.mfa_verified:
        raise HTTPException(status_code=401, detail="mfa_required")
    if session.active_role not in ALL_OPERATOR_ROLES:
        raise HTTPException(status_code=403, detail="operator_only")
    return session


async def tenant_conn(request: Request) -> AsyncIterator:
    """Yield a Postgres connection with `app.tenant_id` GUC bound.

    Requires a valid session on request.state. RLS-protected tables
    honour this GUC.
    """
    session = await require_session(request)
    pool = request.app.state.pool
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)", session.tenant_id
            )
            yield conn
