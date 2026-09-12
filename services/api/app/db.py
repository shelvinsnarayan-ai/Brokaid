"""Async Postgres connection pool for the brokAId API.

Phase 0: connection verification only. Later phases will add
migrations, RLS setup, and the append-only ledger tables.
"""
from __future__ import annotations

import asyncpg
from typing import Optional


_pool: Optional[asyncpg.Pool] = None


async def init_pool(database_url: str) -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=database_url,
            min_size=1,
            max_size=5,
            command_timeout=10,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Postgres pool not initialised")
    return _pool


async def fetch_version(pool: asyncpg.Pool) -> str:
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT version();")
