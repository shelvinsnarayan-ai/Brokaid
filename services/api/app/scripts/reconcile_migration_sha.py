"""Reconcile a stale `_migrations.sha256` after a corrective
migration has already been verified to bring the DB state into
line with the edited file.

Rare, human-approved. NOT part of the automatic startup path.
Emits an audit line to stdout.

Usage:
  DATABASE_URL=... python -m app.scripts.reconcile_migration_sha \\
      010_verifier_tokens.sql 011_verifier_tokens_rls_fix.sql
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg


MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"


async def reconcile(name: str, dsn: str) -> None:
    path = MIGRATIONS_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"migration not found: {path}")
    file_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT sha256 FROM _migrations WHERE name=$1", name,
        )
        if row is None:
            print(f"{name}: no row on record — nothing to reconcile")
            return
        recorded = row["sha256"]
        if recorded == file_sha:
            print(f"{name}: already in sync (sha={file_sha[:16]}...)")
            return
        await conn.execute(
            "UPDATE _migrations SET sha256=$1, applied_at=$2 "
            "WHERE name=$3",
            file_sha, datetime.now(timezone.utc), name,
        )
        print(
            f"{name}: RECONCILED. recorded {recorded[:16]}... → "
            f"file {file_sha[:16]}... at {datetime.now(timezone.utc).isoformat()}"
        )
    finally:
        await conn.close()


async def main() -> None:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        env_path = MIGRATIONS_DIR.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("DATABASE_URL="):
                    dsn = line.split("=", 1)[1].strip()
                    break
    if not dsn:
        print("DATABASE_URL not set", file=sys.stderr); sys.exit(2)
    names = sys.argv[1:]
    if not names:
        print("usage: reconcile_migration_sha <name.sql> [<name.sql>...]",
              file=sys.stderr)
        sys.exit(2)
    for n in names:
        await reconcile(n, dsn)


if __name__ == "__main__":
    asyncio.run(main())
