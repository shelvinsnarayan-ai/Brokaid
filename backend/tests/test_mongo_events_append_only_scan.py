"""Phase R2 — `events` append-only scanner tests.

Two guarantees, both pytest-executable so `pytest` alone gives us
the same signal a CI job would:

  1. `ci/mongo_events_write_scan.js` passes clean on the current
     tree. Every forbidden method against the events collection
     would fail this.

  2. The scanner detects a synthetic bad file — `update_one` on
     `db["events"]` — with a non-zero exit and a useful snippet.

Also asserts the scanner recognises the alternate access patterns
(`db.events`, `db['events']`, `get_collection("events")`) and the
alternate forbidden method names (`bulk_write`, `delete_one`,
`find_one_and_update`, etc.).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCANNER = REPO_ROOT / "ci" / "mongo_events_write_scan.js"


def _node_available() -> bool:
    return shutil.which("node") is not None


pytestmark = pytest.mark.skipif(
    not _node_available(), reason="node absent — cannot exec scanner",
)


def test_scanner_passes_on_current_tree():
    r = subprocess.run(
        ["node", str(SCANNER), str(REPO_ROOT / "backend" / "app")],
        capture_output=True, text=True, timeout=20,
    )
    assert r.returncode == 0, (
        f"scanner failed on clean tree:\nstdout={r.stdout}\nstderr={r.stderr}"
    )
    assert "OK" in r.stdout


def _run_scanner(root):
    return subprocess.run(
        ["node", str(SCANNER), str(root)],
        capture_output=True, text=True, timeout=20,
    )


@pytest.mark.parametrize("access,forbidden", [
    ('db["events"]', "update_one"),
    ("db['events']", "delete_one"),
    ("db.events", "replace_one"),
    ('db["events"]', "bulk_write"),
    ('db["events"]', "find_one_and_update"),
    ('db.events', "find_one_and_delete"),
    ('db["events"]', "drop"),
    ('get_collection("events")', "update_many"),
])
def test_scanner_catches_synthetic_bad(tmp_path, access, forbidden):
    bad = tmp_path / "bad.py"
    bad.write_text(
        "async def x(db):\n"
        f"    await {access}.{forbidden}({{}}, {{'$set': {{'x': 1}}}})\n"
    )
    r = _run_scanner(tmp_path)
    assert r.returncode != 0, (
        f"scanner MISSED {access}.{forbidden}: stdout={r.stdout} stderr={r.stderr}"
    )
    assert "FAILED" in r.stderr
    assert forbidden in r.stderr


def test_scanner_does_not_flag_insert_one(tmp_path):
    good = tmp_path / "good.py"
    good.write_text(
        'async def x(db):\n'
        '    await db["events"].insert_one({"a": 1})\n'
        '    await db.events.insert_one({"b": 2})\n'
    )
    r = _run_scanner(tmp_path)
    assert r.returncode == 0, (
        f"scanner falsely flagged insert_one: stdout={r.stdout} stderr={r.stderr}"
    )


def test_scanner_ignores_forbidden_methods_on_other_collections(tmp_path):
    other = tmp_path / "other.py"
    other.write_text(
        'async def x(db):\n'
        '    await db["tenants"].update_one({}, {"$set": {"a": 1}})\n'
        '    await db["_locks"].delete_one({})\n'
        '    await db.sessions.replace_one({}, {})\n'
    )
    r = _run_scanner(tmp_path)
    assert r.returncode == 0, (
        f"scanner falsely flagged non-events collection: {r.stderr}"
    )


def test_scanner_ignores_mentions_in_comments(tmp_path):
    doc = tmp_path / "docish.py"
    doc.write_text(
        '"""Explain: `db["events"].update_one` is forbidden."""\n'
        '# db["events"].delete_one is banned\n'
        'async def x(db):\n'
        '    await db["events"].insert_one({"a": 1})\n'
    )
    r = _run_scanner(tmp_path)
    assert r.returncode == 0, (
        f"scanner tripped on comments/docstrings: {r.stderr}"
    )
