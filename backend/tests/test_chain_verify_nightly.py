"""Phase-18 pytest for the nightly chain-verify script.

Invokes the script as a subprocess against the live dev DB, asserts
exit 0 and that the generated report file has `all_ok=true` for every
scanned tenant.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


SCRIPT = Path("/app/scripts/chain_verify_nightly.py")
REPORTS_DIR = Path("/app/test_reports")


@pytest.mark.timeout(120)
def test_chain_verify_nightly_exits_zero_and_writes_report():
    """The script must exit 0 against the live dev DB and write a
    same-day report file. Every tenant it scans must verify clean —
    if the DB has drift, this test flips red before the SIRC demo."""
    assert SCRIPT.is_file(), f"script missing: {SCRIPT}"

    # Ensure the live dev DB has at least the canonical demo tenant.
    # After R5c-4b the runtime is Mongo-only; seed once before invoking
    # the nightly script.
    async def _seed_live_dev_db() -> None:
        from motor.motor_asyncio import AsyncIOMotorClient
        from app.seeds.seed_all_mongo import seed_all_mongo

        raw = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017")
        url = raw.replace("://localhost:", "://127.0.0.1:") if "localhost" in raw else raw
        dbn = os.environ.get("DB_NAME", "brokaid")
        client = AsyncIOMotorClient(url, uuidRepresentation="standard")
        try:
            await seed_all_mongo(client[dbn])
        finally:
            client.close()

    asyncio.run(_seed_live_dev_db())

    env = os.environ.copy()
    env.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
    env.setdefault("DB_NAME", "brokaid")
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, (
        f"nightly chain-verify exited {result.returncode}\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    today = datetime.now(timezone.utc).date().isoformat()
    report_path = REPORTS_DIR / f"chain_verify_{today}.json"
    assert report_path.is_file(), f"expected report at {report_path}"
    report = json.loads(report_path.read_text())
    assert report["all_ok"] is True, (
        f"chain_verify_nightly reported failures: {report['results']!r}"
    )
    assert report["tenants_scanned"] >= 1, (
        "expected at least one tenant scanned; report="
        + json.dumps(report, indent=2)
    )
    assert report["hit_bound"] is False, (
        "chain_verify_nightly hit its wall/tenant safety cap on dev DB "
        f"— report={report_path}"
    )
