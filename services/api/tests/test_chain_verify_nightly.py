"""Phase-18 pytest for the nightly chain-verify script.

Invokes the script as a subprocess against the live dev DB, asserts
exit 0 and that the generated report file has `all_ok=true` for every
scanned tenant.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


SCRIPT = Path("/app/scripts/chain_verify_nightly.py")
REPORTS_DIR = Path("/app/test_reports")


@pytest.mark.timeout(90)
def test_chain_verify_nightly_exits_zero_and_writes_report():
    """The script must exit 0 against the live dev DB and write a
    same-day report file. Every tenant it scans must verify clean —
    if the DB has drift, this test flips red before the SIRC demo."""
    assert SCRIPT.is_file(), f"script missing: {SCRIPT}"
    env = os.environ.copy()
    # Point at the live dev DB the seed_all + ci_seed_cli ran against.
    env.setdefault(
        "DATABASE_URL",
        "postgresql://brokaid:brokaid_dev_local@localhost:5432/brokaid",
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=75,
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
    # Bound guards must not have fired on the dev DB.
    assert report["hit_bound"] is False, (
        "chain_verify_nightly hit its wall/tenant safety cap on dev DB "
        f"— report={report_path}"
    )
