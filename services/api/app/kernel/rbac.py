"""Role-based access control.

- `requires_role(*roles)` — FastAPI dependency; 403 + AccessDenied event
  when the caller's active role is not in the allow-set.
- `LOGIN_CAPABLE_ROLES` — the subset of operator roles that can log in.
- `DATA_ONLY_ROLES` — the rest (pod_lead, account_manager, claims_manager,
  tenant_admin, auditor).
"""
from __future__ import annotations

from typing import Callable

from fastapi import HTTPException, Request

from app.auth.sessions import Session
from app.kernel.trail import append


LOGIN_CAPABLE_ROLES = {
    "principal", "deputy", "ae", "accounts", "compliance",
}
DATA_ONLY_ROLES = {
    "pod_lead", "account_manager", "claims_manager", "tenant_admin", "auditor",
}
ALL_OPERATOR_ROLES = LOGIN_CAPABLE_ROLES | DATA_ONLY_ROLES


def requires_role(*allowed: str) -> Callable:
    """Return a FastAPI dependency that 403s when session role isn't allowed."""

    async def _dep(request: Request) -> Session:
        # Local import to avoid a circular dep between rbac and middleware.
        from app.middleware.session import require_operator_session

        # Operator endpoints always require MFA-verified sessions.
        session = await require_operator_session(request)
        if session.active_role not in allowed:
            pool = request.app.state.pool
            await append(
                pool, session.tenant_id, "AccessDenied",
                actor={
                    "kind": "human", "id": session.user_id,
                    "name": session.active_role,
                },
                payload={
                    "path": request.url.path,
                    "method": request.method,
                    "required": list(allowed),
                    "actual_role": session.active_role,
                },
                idempotency_key=(
                    f"access-denied:{session.session_id}:"
                    f"{request.method}:{request.url.path}"
                ),
            )
            raise HTTPException(status_code=403, detail="forbidden")
        return session

    return _dep
