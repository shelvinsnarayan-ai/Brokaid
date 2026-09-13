"""Phase-12 pre-13 — content-checksum drift guard.

Applies a fixture migration, mutates the file on disk, re-runs
migrate → expects `MigrationDrift` with the drifted sha256s in the
message. Restores the file → migrate runs clean.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path

import asyncpg
import pytest

from app.migrate import MIGRATIONS_DIR, MigrationDrift, apply_migrations


FIXTURE_NAME = "099_drift_fixture.sql"
FIXTURE_ORIGINAL = "-- 099 fixture — leave alone.\nSELECT 1;\n"
FIXTURE_MUTATED = "-- 099 fixture — MUTATED. This must trigger drift.\nSELECT 1;\n"


@pytest.fixture
def _fixture_migration(tmp_path):
    """Write, then remove, a temporary drift-fixture migration on disk
    plus its `_migrations` row."""
    path = MIGRATIONS_DIR / FIXTURE_NAME
    original_existed = path.exists()
    if original_existed:
        saved = path.read_bytes()
    path.write_text(FIXTURE_ORIGINAL, encoding="utf-8")
    yield path
    if original_existed:
        path.write_bytes(saved)     # noqa: F821
    else:
        try: path.unlink()
        except FileNotFoundError: pass


async def _cleanup_row(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM _migrations WHERE name=$1", FIXTURE_NAME)


async def test_migration_drift_raises_loud(_fixture_migration, pool) -> None:
    # Extract the DSN from the pool itself so we don't depend on the
    # DATABASE_URL env pointing at the same DB the tests use.
    holder = pool._holders[0] if pool._holders else None
    conn = await pool.acquire()
    try:
        pg_ver = await conn.fetchval("SELECT version()")
        assert pg_ver
    finally:
        await pool.release(conn)
    # Use pool.acquire()-derived DSN via the connection's settings.
    connect_kwargs = pool._connect_kwargs
    dsn = pool._connect_args[0] if pool._connect_args else connect_kwargs.get("dsn")

    await _cleanup_row(pool)
    # 1. Apply the fixture cleanly.
    applied = await apply_migrations(dsn)
    assert FIXTURE_NAME in applied

    original_digest = hashlib.sha256(FIXTURE_ORIGINAL.encode()).hexdigest()

    # 2. Mutate the file → drift.
    _fixture_migration.write_text(FIXTURE_MUTATED, encoding="utf-8")
    mutated_digest = hashlib.sha256(FIXTURE_MUTATED.encode()).hexdigest()
    try:
        with pytest.raises(MigrationDrift) as exc:
            await apply_migrations(dsn)
        msg = str(exc.value)
        assert FIXTURE_NAME in msg
        assert original_digest in msg, f"missing recorded sha in: {msg}"
        assert mutated_digest in msg, f"missing file sha in: {msg}"
        assert "Refusing to start" in msg
        assert re.search(r"phase-?11", msg, re.IGNORECASE), (
            "drift error should reference the phase-11 class of bug"
        )
    finally:
        # 3. Restore the file → migrate runs clean again.
        _fixture_migration.write_text(FIXTURE_ORIGINAL, encoding="utf-8")
        applied_again = await apply_migrations(dsn)
        # Nothing to re-apply (existing row still matches).
        assert FIXTURE_NAME not in applied_again
        await _cleanup_row(pool)
