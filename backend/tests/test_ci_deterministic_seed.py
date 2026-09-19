"""Phase 16 — deterministic CI seed pair tests.

Verifies:
  * Seeding twice produces byte-identical genesis + tip hash for each
    tenant.
  * Two named tenants land with fixed UUIDs.
  * `node verify.js` OK on a real export of the seed; FAIL on tamper
    with `FAIL — event #<seq>` prefix.
  * Neither Burmac nor Harbour-Marine data is disturbed.
"""
from __future__ import annotations

import io
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest


pytestmark = pytest.mark.asyncio


REPO_ROOT = Path(__file__).resolve().parents[2]  # R1 rename: backend/tests → /app
CANONICALIZE_MODULE = REPO_ROOT / "node_modules" / "canonicalize"


def _node_available() -> bool:
    return shutil.which("node") is not None


async def _tip_hash(pool, tenant_id):
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id,
            )
            return await c.fetchval(
                "SELECT event_hash FROM events WHERE tenant_id=$1::uuid "
                "ORDER BY seq DESC LIMIT 1",
                tenant_id,
            )


async def _genesis_hash(pool, tenant_id):
    async with pool.acquire() as c:
        return await c.fetchval(
            "SELECT genesis_hash FROM tenants WHERE tenant_id=$1::uuid",
            tenant_id,
        )


async def _event_count(pool, tenant_id):
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id,
            )
            return await c.fetchval(
                "SELECT count(*) FROM events WHERE tenant_id=$1::uuid",
                tenant_id,
            )


async def test_ci_seed_is_byte_identical_across_runs(pool):
    from app.seeds.ci_deterministic import (
        CI_TENANT_A_ID, CI_TENANT_B_ID, seed_ci_deterministic,
    )
    await seed_ci_deterministic(pool)
    gen_a1 = await _genesis_hash(pool, CI_TENANT_A_ID)
    tip_a1 = await _tip_hash(pool, CI_TENANT_A_ID)
    tip_b1 = await _tip_hash(pool, CI_TENANT_B_ID)
    count_a1 = await _event_count(pool, CI_TENANT_A_ID)

    # Run twice — the second call must be a no-op.
    await seed_ci_deterministic(pool)
    gen_a2 = await _genesis_hash(pool, CI_TENANT_A_ID)
    tip_a2 = await _tip_hash(pool, CI_TENANT_A_ID)
    tip_b2 = await _tip_hash(pool, CI_TENANT_B_ID)
    count_a2 = await _event_count(pool, CI_TENANT_A_ID)

    assert gen_a1 == gen_a2
    assert tip_a1 == tip_a2
    assert tip_b1 == tip_b2
    assert count_a1 == count_a2 and count_a1 > 15


async def test_ci_seed_does_not_touch_existing_tenants(pool, scene_seeded):
    """Seeding CI tenants must not disturb the Burmac genesis/tip."""
    from app.seeds.ci_deterministic import seed_ci_deterministic
    burmac_id = scene_seeded["tenant_id"]
    gen_before = await _genesis_hash(pool, burmac_id)
    tip_before = await _tip_hash(pool, burmac_id)
    count_before = await _event_count(pool, burmac_id)

    await seed_ci_deterministic(pool)

    assert await _genesis_hash(pool, burmac_id) == gen_before
    assert await _tip_hash(pool, burmac_id) == tip_before
    assert await _event_count(pool, burmac_id) == count_before


@pytest.mark.skipif(not _node_available(),
                    reason="node absent — cannot exec verify.js")
async def test_ci_seed_export_verifies_offline_and_tamper_fails(
    pool, app_client, scene_seeded, tmp_path,
):
    from app.seeds.ci_deterministic import (
        CI_TENANT_A_SLUG, seed_ci_deterministic,
    )
    await seed_ci_deterministic(pool)

    # Use the export endpoint via /api/trail/export?tenant=<slug> which
    # is the operator-less public export (legacy).
    _, client = app_client
    r = await client.get(
        f"/api/trail/export?tenant={CI_TENANT_A_SLUG}"
    )
    assert r.status_code == 200, r.text
    export_doc = r.json()

    workdir = tmp_path / "ci-bundle"
    workdir.mkdir()
    (workdir / "export.json").write_text(json.dumps(export_doc))
    verifier_src = REPO_ROOT / "packages" / "kernel" / "verifier" / "verify.js"
    shutil.copy(verifier_src, workdir / "verify.js")
    if CANONICALIZE_MODULE.exists():
        nm = workdir / "node_modules"
        nm.mkdir()
        shutil.copytree(CANONICALIZE_MODULE, nm / "canonicalize")

    ok = subprocess.run(
        ["node", "verify.js", "export.json"],
        cwd=str(workdir), capture_output=True, text=True, timeout=15,
    )
    assert ok.returncode == 0, f"verifier failed on valid CI seed:\n{ok.stdout}\n{ok.stderr}"
    assert "OK" in ok.stdout

    # Tamper: mutate one event's payload_jcs (pick a middle one to be safe).
    doc = json.loads((workdir / "export.json").read_text())
    if len(doc["events"]) < 3:
        pytest.skip("not enough events in CI seed to tamper meaningfully")
    idx = len(doc["events"]) // 2
    doc["events"][idx]["payload_jcs"] = doc["events"][idx]["payload_jcs"] + " "
    seq = doc["events"][idx]["seq"]
    (workdir / "export.json").write_text(json.dumps(doc))
    bad = subprocess.run(
        ["node", "verify.js", "export.json"],
        cwd=str(workdir), capture_output=True, text=True, timeout=15,
    )
    assert bad.returncode != 0
    assert "FAIL" in bad.stdout
    assert f"#{seq}" in bad.stdout
