"""Phase 12.6 — reconcile_migration_sha.py guard tests.

Validates:
  * `--reason` is required.
  * `--reason` shorter than 20 chars is refused.
  * Production env + no `--second-approver` is refused.
  * Non-prod + full args runs, emits a MigrationShaReconciled ledger
    event with all required fields, then updates `_migrations`.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.scripts import reconcile_migration_sha as reconcile_mod


def test_missing_reason_refuses():
    """argparse raises SystemExit when required arg is absent."""
    with pytest.raises(SystemExit) as ei:
        reconcile_mod._parse_args(["010_verifier_tokens.sql"])
    assert ei.value.code == 2


def test_short_reason_refuses():
    args = reconcile_mod._parse_args(
        ["--reason", "too short", "010_verifier_tokens.sql"]
    )
    with pytest.raises(reconcile_mod.ReconcileRefused) as ei:
        reconcile_mod._validate(args, environment="development")
    assert "too short" in str(ei.value).lower()


def test_production_requires_second_approver():
    args = reconcile_mod._parse_args(
        [
            "--reason", "this is a suitably long reason for reconcile testing",
            "010_verifier_tokens.sql",
        ]
    )
    with pytest.raises(reconcile_mod.ReconcileRefused) as ei:
        reconcile_mod._validate(args, environment="production")
    assert "second-approver" in str(ei.value).lower()


def test_production_with_second_approver_accepted():
    args = reconcile_mod._parse_args(
        [
            "--reason", "this is a suitably long reason for reconcile testing",
            "--second-approver", "auditor@example.com",
            "010_verifier_tokens.sql",
        ]
    )
    reconcile_mod._validate(args, environment="production")


@pytest.mark.asyncio
async def test_nonprod_full_args_runs_and_emits_ledger_event(pool):
    """Non-prod: full args → reconciles, emits the ledger event with
    every required field, then updates `_migrations`."""
    migration_name = "099_reconcile_test_fixture.sql"
    stale_sha = "0" * 64
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO _migrations (name, sha256) VALUES ($1, $2) "
            "ON CONFLICT (name) DO UPDATE SET sha256 = EXCLUDED.sha256",
            migration_name, stale_sha,
        )

    fixture_path = reconcile_mod.MIGRATIONS_DIR / migration_name
    fixture_path.write_text("-- reconcile fixture\nSELECT 1;\n")
    expected_new_sha = hashlib.sha256(fixture_path.read_bytes()).hexdigest()

    try:
        approver_actor = {
            "kind": "human", "id": "test-approver",
            "name": "test-approver", "second_approver": None,
        }
        reason = "unit test for phase-12.6 reconciler guard rails"
        summary = await reconcile_mod.reconcile(
            pool, migration_name, reason=reason, approver_actor=approver_actor,
        )

        assert summary["action"] == "reconciled"
        assert summary["old_sha"] == stale_sha
        assert summary["new_sha"] == expected_new_sha
        assert isinstance(summary["event_id"], str) and summary["event_id"]
        assert isinstance(summary["tenant_id"], str) and summary["tenant_id"]

        # Ledger event RLS-guarded: bind the tenant GUC inside a tx.
        async with pool.acquire() as c:
            async with c.transaction():
                await c.execute(
                    "SELECT set_config('app.tenant_id', $1, true)",
                    str(summary["tenant_id"]),
                )
                row = await c.fetchrow(
                    "SELECT type, payload FROM events "
                    "WHERE event_id = $1::uuid",
                    str(summary["event_id"]),
                )
        assert row is not None, "MigrationShaReconciled event must exist"
        assert row["type"] == "MigrationShaReconciled"
        payload = (
            json.loads(row["payload"]) if isinstance(row["payload"], str)
            else row["payload"]
        )
        for k in (
            "migration_name", "old_sha", "new_sha",
            "reason", "approver_actor", "ts",
        ):
            assert k in payload, f"missing {k} in payload"
        assert payload["migration_name"] == migration_name
        assert payload["reason"] == reason
        assert payload["approver_actor"]["id"] == "test-approver"

        # `_migrations.sha256` was updated only after the event.
        async with pool.acquire() as c:
            row = await c.fetchrow(
                "SELECT sha256 FROM _migrations WHERE name=$1", migration_name,
            )
        assert row["sha256"] == expected_new_sha
    finally:
        fixture_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_reconcile_refuses_when_ledger_fails(pool, monkeypatch):
    """If the ledger append raises, the `_migrations` row must NOT be touched."""
    migration_name = "098_reconcile_ledger_fail_fixture.sql"
    stale_sha = "1" * 64
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO _migrations (name, sha256) VALUES ($1, $2) "
            "ON CONFLICT (name) DO UPDATE SET sha256 = EXCLUDED.sha256",
            migration_name, stale_sha,
        )
    fixture_path = reconcile_mod.MIGRATIONS_DIR / migration_name
    fixture_path.write_text("-- ledger-fail fixture\nSELECT 1;\n")

    try:
        from app.kernel import trail as trail_mod

        async def _boom(*a, **kw):
            raise RuntimeError("ledger down for drill")

        # Patch BOTH the direct import in trail AND the aliased import in
        # reconcile_migration_sha (which does `from app.kernel.trail import
        # append as trail_append` at call time).
        monkeypatch.setattr(trail_mod, "append", _boom)

        with pytest.raises(RuntimeError, match="ledger down for drill"):
            await reconcile_mod.reconcile(
                pool, migration_name,
                reason="unit test — ledger failure drill for reconciler",
                approver_actor={"kind": "human", "id": "t", "name": "t"},
            )

        async with pool.acquire() as c:
            row = await c.fetchrow(
                "SELECT sha256 FROM _migrations WHERE name=$1", migration_name,
            )
        assert row["sha256"] == stale_sha, "row must not change if ledger fails"
    finally:
        fixture_path.unlink(missing_ok=True)
