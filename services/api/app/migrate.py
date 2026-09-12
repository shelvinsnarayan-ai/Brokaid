"""Idempotent migration runner.

Applies every `NNN_*.sql` file under `services/api/migrations/` in
numeric order that has not yet been recorded in `_migrations`.

Invoked at API startup (lifespan) and by the test fixture.  Can also
be run standalone:

    python -m app.migrate
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

import asyncpg

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

logger = logging.getLogger("brokaid.migrate")


async def apply_migrations(dsn: str) -> list[str]:
    """Apply all outstanding migrations. Returns the names applied."""
    conn = await asyncpg.connect(dsn)
    applied: list[str] = []
    try:
        # 0. bootstrap the migrations table itself (idempotent)
        bootstrap = (MIGRATIONS_DIR / "000_migrations_table.sql").read_text()
        await conn.execute(bootstrap)

        existing = {
            r["name"]
            for r in await conn.fetch("SELECT name FROM _migrations")
        }

        sql_files = sorted(
            [p for p in MIGRATIONS_DIR.iterdir()
             if p.suffix == ".sql" and p.name != "000_migrations_table.sql"]
        )

        for f in sql_files:
            if f.name in existing:
                continue
            sql = f.read_text()
            digest = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO _migrations (name, sha256) VALUES ($1, $2)",
                    f.name, digest,
                )
            applied.append(f.name)
            logger.info("migration applied: %s", f.name)
        return applied
    finally:
        await conn.close()


async def _main() -> None:
    import sys
    dsn = os.environ["DATABASE_URL"]
    applied = await apply_migrations(dsn)
    for n in applied:
        print(n)
    if not applied:
        print("(no outstanding migrations)")


if __name__ == "__main__":
    import asyncio
    asyncio.run(_main())
