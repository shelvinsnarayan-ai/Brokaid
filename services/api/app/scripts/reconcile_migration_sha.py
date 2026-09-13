"""Reconcile a stale `_migrations.sha256` after a corrective
migration has already been verified to bring the DB state into
line with the edited file.

Rare, human-approved, ledger-audited. NOT part of the automatic
startup path.

Guards (phase 12.6 hardening):
  * `--reason "<text>"` is REQUIRED and must be ≥ 20 characters.
  * In production (`ENVIRONMENT=production`) a `--second-approver
    <email>` is REQUIRED. Non-prod: single approver is fine.
  * A `MigrationShaReconciled { migration_name, old_sha, new_sha,
    reason, approver_actor, ts }` ledger event is emitted under the
    `brokaid-ops` tenant BEFORE the `_migrations` row is updated.
    If the ledger write fails, the reconciler aborts without
    touching `_migrations`.

Usage:
  DATABASE_URL=... python -m app.scripts.reconcile_migration_sha \\
      --reason "phase-11 hotfix 011 corrected DB state; 010 sha stale" \\
      010_verifier_tokens.sql
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"
BROKAID_OPS_TENANT_SLUG = "brokaid-ops"
BROKAID_OPS_TENANT_NAME = "brokAId ops (build/audit)"
MIN_REASON_LEN = 20


class ReconcileRefused(RuntimeError):
    """Guard rejected the invocation. Message describes which guard."""


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="reconcile_migration_sha",
        description=(
            "Update `_migrations.sha256` for a migration whose file "
            "has been edited after apply. Ledger-audited."
        ),
    )
    p.add_argument("names", nargs="+", help="migration filenames (e.g. 010_x.sql)")
    p.add_argument(
        "--reason",
        required=True,
        help=(
            f"Rationale for the reconcile; ≥ {MIN_REASON_LEN} chars. "
            "Written into the MigrationShaReconciled ledger event."
        ),
    )
    p.add_argument(
        "--second-approver",
        default=None,
        help=(
            "Second approver email. REQUIRED when ENVIRONMENT=production."
        ),
    )
    p.add_argument(
        "--approver",
        default=None,
        help=(
            "Primary approver identity. Defaults to $USER or 'brokaid-build'."
        ),
    )
    return p.parse_args(argv)


def _validate(args: argparse.Namespace, environment: str) -> None:
    reason = (args.reason or "").strip()
    if not reason:
        raise ReconcileRefused("--reason is required")
    if len(reason) < MIN_REASON_LEN:
        raise ReconcileRefused(
            f"--reason too short ({len(reason)} < {MIN_REASON_LEN} chars)"
        )
    if environment == "production" and not (args.second_approver or "").strip():
        raise ReconcileRefused(
            "--second-approver is required when ENVIRONMENT=production"
        )


def _actor(args: argparse.Namespace) -> dict:
    primary = (args.approver or os.environ.get("USER") or "brokaid-build").strip()
    return {
        "kind": "human",
        "id": primary,
        "name": primary,
        "second_approver": (args.second_approver or None),
    }


async def _get_or_create_ops_tenant(pool: asyncpg.Pool) -> str:
    """Return the `brokaid-ops` tenant_id; create with genesis on first use."""
    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT tenant_id::text AS tenant_id FROM tenants WHERE slug=$1",
            BROKAID_OPS_TENANT_SLUG,
        )
    if row is not None:
        return row["tenant_id"]
    from app.kernel.trail import create_tenant

    tenant, _ = await create_tenant(
        pool, BROKAID_OPS_TENANT_SLUG, BROKAID_OPS_TENANT_NAME
    )
    return tenant.tenant_id


async def reconcile(
    pool: asyncpg.Pool,
    name: str,
    *,
    reason: str,
    approver_actor: dict,
) -> dict:
    """Reconcile a single migration. Emits ledger event BEFORE update.

    Returns a summary dict for the CLI. Raises FileNotFoundError,
    LookupError, or asyncpg errors on failure — callers propagate.
    """
    path = MIGRATIONS_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"migration not found: {path}")
    file_sha = hashlib.sha256(path.read_bytes()).hexdigest()

    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT sha256 FROM _migrations WHERE name=$1", name,
        )
    if row is None:
        return {"name": name, "action": "noop", "note": "no row on record"}

    recorded = row["sha256"]
    if recorded == file_sha:
        return {"name": name, "action": "noop", "note": "already in sync"}

    tenant_id = await _get_or_create_ops_tenant(pool)
    from app.kernel.trail import append as trail_append

    ts = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    payload = {
        "migration_name": name,
        "old_sha": recorded,
        "new_sha": file_sha,
        "reason": reason,
        "approver_actor": approver_actor,
        "ts": ts,
    }
    # 1. Emit ledger event FIRST. If this raises, we do NOT touch
    #    `_migrations` — the caller sees the error and can retry.
    ev = await trail_append(
        pool,
        tenant_id,
        "MigrationShaReconciled",
        actor=approver_actor,
        payload=payload,
        idempotency_key=f"reconcile:{name}:{recorded}->{file_sha}",
    )

    # 2. Only after the ledger accepted the event do we write.
    async with pool.acquire() as c:
        await c.execute(
            "UPDATE _migrations SET sha256=$1, applied_at=$2 WHERE name=$3",
            file_sha, datetime.now(timezone.utc), name,
        )

    return {
        "name": name,
        "action": "reconciled",
        "old_sha": recorded,
        "new_sha": file_sha,
        "event_id": ev.event_id,
        "event_seq": ev.seq,
        "tenant_id": tenant_id,
    }


def _resolve_dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if dsn:
        return dsn
    env_path = MIGRATIONS_DIR.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip()
    raise ReconcileRefused("DATABASE_URL not set")


async def _run(argv: list[str]) -> int:
    args = _parse_args(argv)
    environment = os.environ.get("ENVIRONMENT", "development")
    try:
        _validate(args, environment)
    except ReconcileRefused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2

    dsn = _resolve_dsn()
    approver_actor = _actor(args)
    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=2)
    try:
        for name in args.names:
            summary = await reconcile(
                pool, name, reason=args.reason, approver_actor=approver_actor,
            )
            if summary["action"] == "noop":
                print(f"{name}: {summary['note']}")
            else:
                print(
                    f"{name}: RECONCILED. "
                    f"{summary['old_sha'][:16]}... → {summary['new_sha'][:16]}... "
                    f"event_id={summary['event_id']} seq={summary['event_seq']}"
                )
    finally:
        await pool.close()
    return 0


def main() -> None:
    argv = sys.argv[1:]
    sys.exit(asyncio.run(_run(argv)))


if __name__ == "__main__":
    main()
