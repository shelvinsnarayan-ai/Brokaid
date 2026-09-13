"""One-shot retrofit: emit `MigrationShaReconciled` for the 010 reconcile
that ran locally BEFORE the guard existed.

Runs directly against the running DB. Reads settings via the same .env
the API uses. Idempotent: uses a deterministic idempotency_key, so
re-running is safe (second call raises UniqueViolationError which we
catch and report as "already retrofitted").
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
from dotenv import load_dotenv


OLD_SHA = "0100f05fa06504a83295ac4a135507ca4e8adf81491a435edc510006d52e3278"
NEW_SHA = "f759583674b30dd639e5eddc906ab62c4f7d9c218c6c918dc9d34665d899a022"
MIGRATION_NAME = "010_verifier_tokens.sql"
REASON = (
    "phase 11 hotfix 011 corrected the DB via a fresh policy; the "
    "recorded sha for 010 was stale but DB state was already correct via 011"
)
APPROVER_ACTOR = {
    "kind": "human",
    "id": "brokaid-build",
    "name": "brokaid-build (retrofit — reconciled before the audit guard existed)",
    "retrofit": True,
}


async def _main() -> int:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    dsn = os.environ["DATABASE_URL"]
    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=2)
    try:
        from app.routes.auth_dev import _get_or_create_ops_tenant
        from app.kernel.trail import append as trail_append

        tenant_id = await _get_or_create_ops_tenant(pool)
        ts = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        payload = {
            "migration_name": MIGRATION_NAME,
            "old_sha": OLD_SHA,
            "new_sha": NEW_SHA,
            "reason": REASON,
            "approver_actor": APPROVER_ACTOR,
            "ts": ts,
        }
        idempotency_key = (
            f"retrofit-reconcile:{MIGRATION_NAME}:{OLD_SHA}->{NEW_SHA}"
        )
        try:
            ev = await trail_append(
                pool,
                tenant_id,
                "MigrationShaReconciled",
                actor=APPROVER_ACTOR,
                payload=payload,
                idempotency_key=idempotency_key,
            )
        except asyncpg.UniqueViolationError:
            print("ALREADY_RETROFITTED (idempotency key already present)")
            return 0

        print(f"RETROFIT_EVENT_ID={ev.event_id}")
        print(f"RETROFIT_EVENT_SEQ={ev.seq}")
        print(f"RETROFIT_TENANT_ID={tenant_id}")
        print(f"RETROFIT_EVENT_HASH={ev.event_hash}")
        print("--- payload ---")
        import json
        print(json.dumps(ev.payload, indent=2, sort_keys=True))
    finally:
        await pool.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
