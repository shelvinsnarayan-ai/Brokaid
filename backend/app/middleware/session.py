"""Session middleware + dependencies (R5c-4b — Mongo-only)."""
from __future__ import annotations

from fastapi import HTTPException, Request

from app.auth import sessions as sess


async def _load_session(request: Request):
    cookie = request.cookies.get(sess.COOKIE_NAME)
    if not cookie:
        return None
    from app.kernel import identity
    store = getattr(request.app.state, "mongo_db", None)
    if store is None:
        return None
    try:
        return await identity.read_session(store, cookie)
    except Exception:
        return None


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
    active_role required."""
    from app.kernel.rbac import ALL_OPERATOR_ROLES

    session = await require_session(request)
    if not session.mfa_verified:
        raise HTTPException(status_code=401, detail="mfa_required")
    if session.active_role not in ALL_OPERATOR_ROLES:
        raise HTTPException(status_code=403, detail="operator_only")
    return session
