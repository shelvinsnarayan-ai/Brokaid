"""Phase R5c-3b — scripts/build_demo_bundle.py + chain_verify_nightly.py pins.

Coverage
--------
- Two consecutive bundle builds produce byte-identical `ci-alpha.zip`
  (determinism).
- Archive `ci-alpha.pg.zip` root hash unchanged at `92e61063...`.
- Live Mongo bundle chain verifies clean under the offline verifier.
- `scripts/build_demo_bundle.py` has zero `(mongo)` / `read_events(pool)`
  / plain `seed_ci_deterministic` references (regex assert on the
  file bytes).
- `scripts/chain_verify_nightly.py` has zero `(mongo)` / `pool`
  references.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from pathlib import Path

import pytest


CI_A_ID = ""  # unused placeholder to keep pytest-asyncio quiet if imported


REPO_ROOT = Path("/app")
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build_demo_bundle.py"
NIGHTLY_SCRIPT = REPO_ROOT / "scripts" / "chain_verify_nightly.py"
CLEAN_ZIP = REPO_ROOT / "docs" / "demo_bundle" / "ci-alpha.zip"
PG_ARCHIVE_ZIP = REPO_ROOT / "docs" / "demo_bundle" / "ci-alpha.pg.zip"
PG_ARCHIVE_EXPECTED_ROOT = (
    "92e61063c8d225f40bebd2ae794d9d5810bbf9a48d92ba056845c68b6ac1b5d8"
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _extract_root(zip_path: Path) -> str:
    with zipfile.ZipFile(zip_path, "r") as z:
        with z.open("export.json") as f:
            doc = json.loads(f.read().decode("utf-8"))
    events = doc.get("events", [])
    return events[-1]["event_hash"] if events else ""


# ---- Pin 1: `build_demo_bundle.py` free of PG-only symbols ---------
def test_pin_r5c3b_build_script_has_no_pg_symbols():
    src = BUILD_SCRIPT.read_text()
    # Reject actual imports/usages, not docstring word appearances.
    assert not re.search(r"\bimport\s+(mongo)\b", src)
    assert not re.search(r"\bfrom\s+(mongo)\b", src)
    assert not re.search(r"\b(mongo)\.[A-Za-z]", src)
    assert "read_events(pool" not in src
    # allow only the _mongo variant of the seeder
    plain_hits = re.findall(
        r"\bseed_ci_deterministic\b(?!_mongo)", src,
    )
    assert plain_hits == [], f"plain seeder still referenced: {plain_hits}"
    assert "seed_ci_deterministic_mongo" in src


# ---- Pin 2: `chain_verify_nightly.py` free of PG-only symbols ------
def test_pin_r5c3b_nightly_script_has_no_pg_symbols():
    src = NIGHTLY_SCRIPT.read_text()
    assert not re.search(r"\bimport\s+(mongo)\b", src)
    assert not re.search(r"\bfrom\s+(mongo)\b", src)
    assert not re.search(r"\b(mongo)\.[A-Za-z]", src)
    assert "read_events(pool" not in src
    # Under R5c-3b the nightly must reference both roots — the
    # archived PG bundle load path and the live Mongo verify path.
    assert "PG_ARCHIVE_ZIP" in src
    assert "ci-alpha.pg.zip" in src
    assert "mongo_root=" in src
    assert "pg_archive_root=" in src


@pytest.mark.asyncio
async def test_pin_r5c3b_bundle_build_is_deterministic_marker(): pass  # placeholder marker anchor


# ---- Pin 3: Archive `ci-alpha.pg.zip` unchanged --------------------
def test_pin_r5c3b_pg_archive_root_unchanged():
    assert PG_ARCHIVE_ZIP.exists(), (
        f"pg archive missing: {PG_ARCHIVE_ZIP}"
    )
    root = _extract_root(PG_ARCHIVE_ZIP)
    assert root == PG_ARCHIVE_EXPECTED_ROOT, (
        f"pg archive root drifted: expected {PG_ARCHIVE_EXPECTED_ROOT}, "
        f"got {root}"
    )


# ---- Pin 4: pg archive verifies clean under offline verifier -------
def test_pin_r5c3b_pg_archive_verifies_clean():
    from app.kernel.verifier import verify_events
    with zipfile.ZipFile(PG_ARCHIVE_ZIP, "r") as z:
        with z.open("export.json") as f:
            doc = json.loads(f.read().decode("utf-8"))
    result = verify_events(doc.get("events", []))
    assert result.ok is True
    assert result.root_hash == PG_ARCHIVE_EXPECTED_ROOT


# ---- Pin 5: `build_bundles()` deterministic across two runs --------
@pytest.mark.asyncio
async def test_pin_r5c3b_bundle_build_is_deterministic(monkeypatch):
    """Import `build_bundles` from the script and call it twice
    against a scratch DB. Assert byte-identical zips + root hash
    matches the PG archive (R2 byte-identical hash guarantee).
    """
    import importlib.util
    monkeypatch.setenv(
        "BROKAID_DEMO_BUNDLE_DB", "brokaid_demo_bundle_test",
    )
    monkeypatch.setenv(
        "MONGO_URL", os.environ.get("MONGO_URL", "mongodb://localhost:27017"),
    )

    # Fresh module import so `os.environ["DB_NAME"]` re-evaluates.
    spec = importlib.util.spec_from_file_location(
        "build_demo_bundle_r5c3b_pin", BUILD_SCRIPT,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    result_a = await mod.build_bundles()
    result_b = await mod.build_bundles()

    # Byte-identical clean bundles across runs.
    assert _sha256(result_a["clean_bytes"]) == _sha256(result_b["clean_bytes"])
    # And the tampered variant is deterministic too.
    assert _sha256(result_a["tampered_bytes"]) == _sha256(
        result_b["tampered_bytes"],
    )
    # Root hash matches the PG archive by construction (R2 guarantee).
    assert result_a["root_hash"] == PG_ARCHIVE_EXPECTED_ROOT
    assert result_a["n_events"] > 0


# ---- Pin 6: live Mongo bundle verifies under offline verifier ------
@pytest.mark.asyncio
async def test_pin_r5c3b_new_mongo_bundle_verifies_clean(monkeypatch):
    """Rebuild once against a scratch DB; run the same offline
    `verify_events` against the produced export.json; must PASS."""
    import importlib.util
    monkeypatch.setenv(
        "BROKAID_DEMO_BUNDLE_DB", "brokaid_demo_bundle_test",
    )
    spec = importlib.util.spec_from_file_location(
        "build_demo_bundle_r5c3b_pin_v", BUILD_SCRIPT,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    result = await mod.build_bundles()
    with zipfile.ZipFile(BytesIO(result["clean_bytes"])) as z:
        with z.open("export.json") as f:
            doc = json.loads(f.read().decode("utf-8"))
    from app.kernel.verifier import verify_events
    r = verify_events(doc.get("events", []))
    assert r.ok is True
    assert r.root_hash == PG_ARCHIVE_EXPECTED_ROOT


# ---- test helper import at bottom to keep imports tidy at top -----
from io import BytesIO  # noqa: E402
