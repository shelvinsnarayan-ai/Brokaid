"""Session lifecycle — create, load, revoke.

Sessions are stored server-side; the browser holds only the session_id
UUID as an HttpOnly cookie. Cross-tenant isolation is enforced downstream
by setting `app.tenant_id` GUC from the session on every DB call that
touches tenant-scoped tables.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import asyncpg


COOKIE_NAME = "brokaid_session"
SESSION_TTL = timedelta(hours=12)


@dataclass(frozen=True)
class Session:
    session_id: str
    user_id: str
    tenant_id: str
    brokerage_id: Optional[str]
    client_id: Optional[str]
    active_role: str
    mfa_verified: bool
    expires_at: datetime


async def create(
    pool: asyncpg.Pool,
    *,
    user_id: str,
    tenant_id: str,
    active_role: str,
    brokerage_id: Optional[str] = None,
    client_id: Optional[str] = None,
    mfa_verified: bool = False,
    ttl: timedelta = SESSION_TTL,
) -> Session:
    session_id = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc) + ttl
    async with pool.acquire() as c:
        await c.execute(
            """
            INSERT INTO sessions
              (session_id, user_id, tenant_id, brokerage_id, client_id,
               active_role, mfa_verified, expires_at)
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7, $8)
            """,
            session_id, user_id, tenant_id,
            brokerage_id, client_id, active_role, mfa_verified, expires_at,
        )
    return Session(
        session_id=session_id, user_id=user_id, tenant_id=tenant_id,
        brokerage_id=brokerage_id, client_id=client_id,
        active_role=active_role, mfa_verified=mfa_verified,
        expires_at=expires_at,
    )


async def load(pool: asyncpg.Pool, session_id: str) -> Optional[Session]:
    try:
        uuid.UUID(session_id)
    except (TypeError, ValueError):
        return None
    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT session_id, user_id, tenant_id, brokerage_id, client_id, "
            "active_role, mfa_verified, expires_at, revoked_at "
            "FROM sessions WHERE session_id = $1::uuid",
            session_id,
        )
    if row is None:
        return None
    if row["revoked_at"] is not None:
        return None
    if row["expires_at"] < datetime.now(timezone.utc):
        return None
    return Session(
        session_id=str(row["session_id"]),
        user_id=str(row["user_id"]),
        tenant_id=str(row["tenant_id"]),
        brokerage_id=str(row["brokerage_id"]) if row["brokerage_id"] else None,
        client_id=str(row["client_id"]) if row["client_id"] else None,
        active_role=row["active_role"],
        mfa_verified=row["mfa_verified"],
        expires_at=row["expires_at"],
    )


async def mark_mfa_verified(pool: asyncpg.Pool, session_id: str) -> None:
    async with pool.acquire() as c:
        await c.execute(
            "UPDATE sessions SET mfa_verified = TRUE WHERE session_id = $1::uuid",
            session_id,
        )


async def revoke(pool: asyncpg.Pool, session_id: str) -> None:
    async with pool.acquire() as c:
        await c.execute(
            "UPDATE sessions SET revoked_at = NOW() WHERE session_id = $1::uuid",
            session_id,
        )


async def update_active_context(
    pool: asyncpg.Pool,
    session_id: str,
    *,
    tenant_id: str,
    client_id: str,
    active_role: str,
) -> None:
    async with pool.acquire() as c:
        await c.execute(
            """
            UPDATE sessions
               SET tenant_id = $2::uuid, client_id = $3::uuid, active_role = $4
             WHERE session_id = $1::uuid
            """,
            session_id, tenant_id, client_id, active_role,
        )
