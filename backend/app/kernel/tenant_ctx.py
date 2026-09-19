"""brokAId Phase R3 — request-scoped tenant context.

A `ContextVar` holds the current tenant_id for the duration of a
request (or an explicitly-scoped block for seeds/tests/off-request
work). `get_tenant_id()` fails loud if the context is empty; that
failure is the acceptance-gate primitive of R3 — a Mongo read/write
without a tenant context CANNOT silently return cross-tenant data
because the earliest touch of the safe wrapper raises.

Wiring
------
- `app.middleware.tenant.TenantContextMiddleware` sets the var on
  request entry from the session cookie, resets on exit.
- Off-request callers (seeds, tests, cron jobs) use
  `async with use_tenant(tenant_id): ...` — the same primitive.

ContextVar / asyncio note
-------------------------
`ContextVar` is copied per task boundary, so concurrent requests
in the same event loop see independent values. Tested under
Starlette + httpx in `tests/test_tenant_isolation.py`.
"""
from __future__ import annotations

import contextlib
from contextvars import ContextVar, Token
from typing import AsyncIterator, Optional


class NoTenantContextError(RuntimeError):
    """Raised when a tenant-scoped operation runs with no tenant context.

    R3 fail-loud primitive. Never catch this to fall back — the
    correct response is to attach a session (via middleware) or to
    scope the caller inside `use_tenant(...)`.
    """


class TenantMismatchError(ValueError):
    """Raised when an explicit tenant filter contradicts the context.

    Signals a genuine cross-tenant access attempt — either a bug in
    the caller or an actual leak attempt. Never silence.
    """


# Sentinel: `None` means "no active tenant context".
current_tenant_id: ContextVar[Optional[str]] = ContextVar(
    "brokaid_current_tenant_id", default=None,
)


def get_tenant_id() -> str:
    """Return the current tenant_id or raise `NoTenantContextError`.

    Never returns None. Never falls back. Every callsite in the
    tenant-safe wrapper hits this and blows up if the context is
    empty — which is exactly the guarantee R3 promises.
    """
    tid = current_tenant_id.get()
    if tid is None:
        raise NoTenantContextError(
            "no tenant context bound. Either the request skipped the "
            "TenantContextMiddleware, or a seed / test / cron caller "
            "forgot to wrap the block in `async with use_tenant(tid)`."
        )
    return tid


def has_tenant_context() -> bool:
    """True iff a tenant_id is currently bound.

    Cheap probe for callers that legitimately want to branch on
    presence (e.g. a route handler checking whether it's inside a
    session or a public path). Do NOT use this to skip an
    isolation check — that would defeat the fail-loud guarantee.
    """
    return current_tenant_id.get() is not None


@contextlib.asynccontextmanager
async def use_tenant(tenant_id: str) -> AsyncIterator[str]:
    """Async context manager binding `tenant_id` for the enclosed block.

    Use in seeds, tests, cron/job runners, and any code path that
    isn't inside a normal request/response cycle. Reset is
    exception-safe.
    """
    if not tenant_id or not isinstance(tenant_id, str):
        raise ValueError(f"use_tenant() requires a non-empty str, got {tenant_id!r}")
    token: Token = current_tenant_id.set(tenant_id)
    try:
        yield tenant_id
    finally:
        current_tenant_id.reset(token)


def set_tenant_unsafe(tenant_id: Optional[str]) -> Token:
    """Set the context var and return the reset token.

    Only for middleware — normal callers should use `use_tenant()`
    which handles the reset for you.
    """
    return current_tenant_id.set(tenant_id)


def reset_tenant_unsafe(token: Token) -> None:
    current_tenant_id.reset(token)
