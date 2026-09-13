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


class MigrationDrift(RuntimeError):
    """A migration file's SHA-256 no longer matches what was recorded
    when it was first applied. Loud, unmistakable, refuses to start.

    This is the phase-11 class of bug: editing an already-applied
    migration silently drifts DBs from source of truth. See
    services/api/migrations/011_verifier_tokens_rls_fix.sql for the
    hotfix that closed the symptom; this guard closes the class.
    """


async def apply_migrations(dsn: str) -> list[str]:
    """Apply all outstanding migrations. Returns the names applied."""
    conn = await asyncpg.connect(dsn)
    applied: list[str] = []
    try:
        # 0. bootstrap the migrations table itself (idempotent)
        bootstrap = (MIGRATIONS_DIR / "000_migrations_table.sql").read_text()
        await conn.execute(bootstrap)

        existing_rows = await conn.fetch("SELECT name, sha256 FROM _migrations")
        existing: dict[str, str] = {r["name"]: r["sha256"] for r in existing_rows}

        sql_files = sorted(
            [p for p in MIGRATIONS_DIR.iterdir()
             if p.suffix == ".sql" and p.name != "000_migrations_table.sql"]
        )

        for f in sql_files:
            sql = f.read_text()
            digest = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            recorded = existing.get(f.name)
            if recorded is not None:
                if recorded != digest:
                    raise MigrationDrift(
                        f"Migration {f.name!r} has drifted since it was applied. "
                        f"Recorded sha256={recorded} file sha256={digest}. "
                        f"Refusing to start. Create a new numbered migration "
                        f"or restore the file. (This is the phase-11 class of "
                        f"bug — see docs/GOVERNANCE.md.)"
                    )
                continue
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
