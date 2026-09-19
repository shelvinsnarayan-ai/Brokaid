"""Phase R3 — raw-collection scanner tests.

Two guarantees, pytest-invokable so the same signal a CI job would
give lives in the default `pytest` run:

  1. `ci/mongo_raw_collection_scan.js` passes clean on the current
     backend tree. Every raw `db["events"]`-style access outside the
     wrapper (or a marked file) would trip this.

  2. The scanner catches synthetic-bad files that reach into raw
     collections — `db["events"]`, `db.users`, `AsyncIOMotorCollection`
     import — with a non-zero exit and a useful snippet.

Also verifies that the `# tenant-safe: system-only` marker
allow-lists a file, and that comments / docstrings don't self-trip.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCANNER = REPO_ROOT / "ci" / "mongo_raw_collection_scan.js"


def _node_available() -> bool:
    return shutil.which("node") is not None


pytestmark = pytest.mark.skipif(
    not _node_available(), reason="node absent — cannot exec scanner",
)


def _run(root):
    return subprocess.run(
        ["node", str(SCANNER), str(root)],
        capture_output=True, text=True, timeout=20,
    )


def test_scanner_passes_on_current_tree():
    r = _run(REPO_ROOT / "backend" / "app")
    assert r.returncode == 0, (
        f"scanner failed on clean tree:\nstdout={r.stdout}\nstderr={r.stderr}"
    )
    assert "OK" in r.stdout


@pytest.mark.parametrize("body,expected", [
    ('async def x(db):\n    return await db["events"].find_one({})\n', "db[<name>]"),
    ("async def x(db):\n    return await db['clients'].find_one({})\n", "db[<name>]"),
    ("async def x(db):\n    return await db.users.find_one({})\n", "db.users"),
    ("async def x(db):\n    return await db.events.insert_one({})\n", "db.events"),
    ("async def x(db):\n    return await db.proj_queue.find({})\n", "db.proj_<name>"),
    ("from motor.motor_asyncio import AsyncIOMotorCollection\n", "AsyncIOMotorCollection"),
])
def test_scanner_catches_raw_access(tmp_path, body, expected):
    bad = tmp_path / "bad.py"
    bad.write_text(body)
    r = _run(tmp_path)
    assert r.returncode != 0, (
        f"scanner MISSED {expected!r}: stdout={r.stdout} stderr={r.stderr}"
    )
    assert "FAILED" in r.stderr
    assert expected in r.stderr


def test_scanner_ignores_system_collections(tmp_path):
    good = tmp_path / "ok.py"
    good.write_text(
        'async def x(db):\n'
        '    await db["_locks"].find_one({})\n'
        '    await db["_counters"].find_one({})\n'
    )
    r = _run(tmp_path)
    assert r.returncode == 0, (
        f"scanner falsely flagged system collections: {r.stderr}"
    )


def test_marker_allows_raw_access(tmp_path):
    marked = tmp_path / "marked.py"
    marked.write_text(
        '# tenant-safe: system-only\n'
        '"""Legitimate system-only file."""\n'
        'async def x(db):\n'
        '    return await db["events"].find_one({})\n'
    )
    r = _run(tmp_path)
    assert r.returncode == 0, (
        f"scanner ignored the # tenant-safe: system-only marker: {r.stderr}"
    )


def test_comments_and_docstrings_do_not_trip(tmp_path):
    docish = tmp_path / "docish.py"
    docish.write_text(
        '"""Explain: db["events"].find_one is banned outside wrapper."""\n'
        '# db.users is also forbidden without marker\n'
        'from app.db.tenant_safe import wrap\n'
        'async def x(mongo):\n'
        '    safe = wrap(mongo)\n'
        '    return await safe.events.find_one({})\n'
    )
    r = _run(tmp_path)
    assert r.returncode == 0, (
        f"scanner tripped on comments/docstrings: {r.stderr}"
    )
