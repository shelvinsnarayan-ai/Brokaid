"""Magic-link token generation, hashing, and storage helpers.

The raw token is a 32-byte URL-safe string handed out once. Only its
SHA-256 hash is stored. Tokens expire after 15 minutes and are
single-use (marked via `used_at`).
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import asyncpg


TOKEN_TTL = timedelta(minutes=15)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def issue(pool: asyncpg.Pool, user_id: str) -> tuple[str, datetime]:
    """Issue a fresh token for `user_id`. Returns (raw_token, expires_at)."""
    raw = secrets.token_urlsafe(32)
    tok_hash = _hash(raw)
    expires = datetime.now(timezone.utc) + TOKEN_TTL
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO magic_link_tokens (token_hash, user_id, expires_at) "
            "VALUES ($1, $2::uuid, $3)",
            tok_hash, user_id, expires,
        )
    return raw, expires


async def consume(
    pool: asyncpg.Pool, raw_token: str
) -> tuple[Optional[str], Optional[str]]:
    """Validate + mark a token used. Returns (user_id, error).

    Errors:
      - 'unknown'       — token hash not in table.
      - 'expired'       — expires_at < now.
      - 'already_used'  — used_at already set.

    Only on success is user_id returned and the row atomically flipped.
    """
    tok_hash = _hash(raw_token)
    async with pool.acquire() as c:
        async with c.transaction():
            row = await c.fetchrow(
                "SELECT user_id, expires_at, used_at FROM magic_link_tokens "
                "WHERE token_hash = $1 FOR UPDATE",
                tok_hash,
            )
            if row is None:
                return None, "unknown"
            if row["used_at"] is not None:
                return None, "already_used"
            if row["expires_at"] < datetime.now(timezone.utc):
                return None, "expired"
            await c.execute(
                "UPDATE magic_link_tokens SET used_at = NOW() WHERE token_hash = $1",
                tok_hash,
            )
            return str(row["user_id"]), None
