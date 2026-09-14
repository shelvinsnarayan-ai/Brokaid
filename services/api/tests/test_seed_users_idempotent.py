"""Phase 16 — seed_users idempotency + full-suite green-run contract.

The reported bug: `seed_users.seed()` produced an FK violation on
`user_brokerage_roles.brokerage_id` when a fresh pool ran after a
TRUNCATE-CASCADE from a previous test. Root cause: SELECT-then-INSERT
race in `_ensure_brokerage`. Fix: `INSERT ... ON CONFLICT (slug) DO
UPDATE ... RETURNING brokerage_id` inside a single transaction.

Tests:
  * Running seed() twice in a row on the same pool succeeds and yields
    identical brokerage_id.
  * Concurrent seed() calls succeed and converge on the same
    brokerage_id.
  * A follow-on `_ensure_operator` insert never violates the FK.
"""
from __future__ import annotations

import asyncio

import pytest

from app.seeds import seed_users


pytestmark = pytest.mark.asyncio


async def test_seed_twice_is_idempotent(pool):
    r1 = await seed_users.seed(pool)
    r2 = await seed_users.seed(pool)
    assert r1["brokerage_id"] == r2["brokerage_id"]
    assert r1["tenant_id"] == r2["tenant_id"]
    assert {op["email"] for op in r1["operators"]} == {
        op["email"] for op in r2["operators"]
    }


async def test_concurrent_seed_converges(pool):
    r1, r2, r3 = await asyncio.gather(
        seed_users.seed(pool),
        seed_users.seed(pool),
        seed_users.seed(pool),
    )
    ids = {r1["brokerage_id"], r2["brokerage_id"], r3["brokerage_id"]}
    assert len(ids) == 1, (
        f"concurrent seed() must converge on one brokerage_id; got {ids}"
    )


async def test_operator_insert_never_violates_fk(pool):
    """After seed, every operator has a live user_brokerage_roles row."""
    r = await seed_users.seed(pool)
    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT COUNT(*) AS n FROM user_brokerage_roles r "
            " JOIN brokerages b ON b.brokerage_id = r.brokerage_id "
            " WHERE b.brokerage_id = $1::uuid",
            r["brokerage_id"],
        )
    assert row["n"] == len(r["operators"]), (
        f"expected {len(r['operators'])} operator role rows, got {row['n']}"
    )
