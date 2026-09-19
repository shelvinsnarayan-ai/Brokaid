"""Phase 13.5 — deny-list regression guard, runs the CI scanner over
the built `frontend` bundle from within pytest.

This test is skipped if `frontend/dist` is absent — CI is expected to
run `yarn build` before pytest. When `dist` is present, the test
fails on any deny-list match (internal agent name from the closed
set, pinned model id, or the generic `/[a-z]+AI[a-z]+/` pattern
outside the brand allow-list).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]  # R1 rename: backend/tests → /app
DIST = REPO_ROOT / "frontend" / "dist"
SCANNER = REPO_ROOT / "ci" / "deny-list.js"


@pytest.mark.skipif(
    not DIST.exists(), reason="frontend/dist absent — run `yarn build` first",
)
def test_no_agent_names_in_claim_bundle():
    """Run ci/deny-list.js against frontend/dist. Exit code 0 = pass."""
    assert SCANNER.exists(), f"scanner missing at {SCANNER}"
    result = subprocess.run(
        ["node", str(SCANNER), str(DIST)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        "deny-list flagged the built bundle:\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )
    # Positive assertion: scanner reports OK with at least one file scanned.
    assert "deny-list: OK" in result.stdout, (
        f"unexpected scanner output: {result.stdout!r}"
    )
