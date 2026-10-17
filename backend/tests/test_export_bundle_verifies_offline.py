"""Phase 15 — the exported bundle verifies offline. Tampered bundle
fails at the exact seq index that was mutated.
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


@pytest.mark.skipif(
    not _node_available(), reason="node runtime absent — cannot run offline verifier",
)
async def test_export_bundle_verifies_offline(app_client, scene_seeded, tmp_path):
    _, client = app_client
    op = next(o for o in scene_seeded["operators"] if o["role"] == "principal")
    r = await client.post("/api/auth/dev/impersonate", json={"email": op["email"]})
    assert r.status_code == 200

    r = await client.get("/api/trail/export/bundle")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"

    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(z.namelist())
    assert {"export.json", "verify.js", "README.md",
            "verifier_binary_sha256.txt"} <= names, (
        f"bundle missing expected files; got {names}"
    )

    workdir = tmp_path / "unzipped"
    workdir.mkdir()
    z.extractall(workdir)

    # Prepare node_modules — the verifier imports canonicalize.
    if CANONICALIZE_MODULE.exists():
        nm = workdir / "node_modules"
        nm.mkdir()
        shutil.copytree(CANONICALIZE_MODULE, nm / "canonicalize")

    verify_script = workdir / "verify.js"
    export_path = workdir / "export.json"

    result = subprocess.run(
        ["node", str(verify_script), str(export_path)],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, (
        f"offline verifier failed on VALID export.\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "OK" in result.stdout, (
        f"expected OK on valid export; got {result.stdout!r}"
    )

    # Tamper: mutate one event's payload_jcs field.
    doc = json.loads(export_path.read_text())
    if not doc["events"]:
        pytest.skip("no events to tamper with")
    target_seq = doc["events"][0]["seq"]
    doc["events"][0]["payload_jcs"] = (
        doc["events"][0]["payload_jcs"] + " "
    )  # add a trailing space
    export_path.write_text(json.dumps(doc))

    result2 = subprocess.run(
        ["node", str(verify_script), str(export_path)],
        capture_output=True, text=True, timeout=15,
    )
    assert result2.returncode != 0, (
        f"verifier should FAIL on tampered export; stdout={result2.stdout}"
    )
    assert "FAIL" in result2.stdout, (
        f"expected FAIL keyword; got {result2.stdout!r}"
    )
    assert f"#{target_seq}" in result2.stdout, (
        f"expected FAIL to reference #seq={target_seq}; got {result2.stdout!r}"
    )
