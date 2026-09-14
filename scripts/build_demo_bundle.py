"""Phase 17 — build the pre-signed offline verifier bundles for SIRC.

Emits two artefacts under docs/demo_bundle/:
  * ci-alpha.zip           — clean bundle, verifies OK
  * ci-alpha-tampered.zip  — one byte flipped mid-chain, verifies FAIL

Design goals:
  * No HTTP path. Directly seed the CI deterministic pair and read the
    ledger via app.kernel.trail.read_events. Bypasses auth entirely.
  * Bit-for-bit identical to what /api/trail/export/bundle streams — we
    reuse `_build_export_doc` + the exact readme text + verifier binary.
  * Tampered bundle carries a one-line inner README explaining WHAT was
    flipped so we are not fooling ourselves about the failure mode.
  * Runs standalone: `python scripts/build_demo_bundle.py`.

Fictional data only. No real client information is touched.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

# ---- Wire up the API package ---------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICES_API = REPO_ROOT / "services" / "api"
sys.path.insert(0, str(SERVICES_API))
load_dotenv(SERVICES_API / ".env")

from app.kernel.trail import read_events  # noqa: E402
from app.routes.trail import (  # noqa: E402
    VERIFIER_JS,
    _build_export_doc,
    _README_TEXT,
    _verifier_sha256,
)
from app.seeds.ci_deterministic import (  # noqa: E402
    CI_TENANT_A_SLUG,
    seed_ci_deterministic,
)


OUT_DIR = REPO_ROOT / "docs" / "demo_bundle"
CLEAN_ZIP = OUT_DIR / "ci-alpha.zip"
TAMPERED_ZIP = OUT_DIR / "ci-alpha-tampered.zip"


def _make_bundle_bytes(export_doc: dict, filename: str) -> bytes:
    verifier_bytes = (
        VERIFIER_JS.read_bytes() if VERIFIER_JS.exists()
        else b"// verifier missing\n"
    )
    verifier_sha = _verifier_sha256()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("export.json", json.dumps(export_doc, indent=2))
        z.writestr("verify.js", verifier_bytes)
        z.writestr("verifier_binary_sha256.txt", verifier_sha + "\n")
        z.writestr("README.md", _README_TEXT.format(
            filename=filename,
            client_display="ci-alpha (fictional CI tenant)",
            event_count=len(export_doc["events"]),
            verifier_sha=verifier_sha,
        ))
    return buf.getvalue()


def _make_tampered_bytes(export_doc: dict, filename: str) -> tuple[bytes, int]:
    """Flip one byte in a mid-chain event's payload_jcs.

    Returns (bundle_bytes, target_seq) so the caller can print the exact
    seq that will fail. Uses the middle event (not first, not last) so
    the failure lands 'mid-chain' as the runbook advertises.
    """
    doc = json.loads(json.dumps(export_doc))  # deep copy
    events = doc["events"]
    if not events:
        raise RuntimeError("ci-alpha has no events; cannot tamper")
    idx = len(events) // 2
    target_seq = events[idx]["seq"]
    # Append a trailing space to payload_jcs — surgical, one byte, and
    # unambiguously changes the canonicalised form so verify.js fails
    # at the payload_jcs comparison BEFORE the hash check.
    events[idx]["payload_jcs"] = events[idx]["payload_jcs"] + " "
    verifier_bytes = (
        VERIFIER_JS.read_bytes() if VERIFIER_JS.exists()
        else b"// verifier missing\n"
    )
    verifier_sha = _verifier_sha256()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("export.json", json.dumps(doc, indent=2))
        z.writestr("verify.js", verifier_bytes)
        z.writestr("verifier_binary_sha256.txt", verifier_sha + "\n")
        z.writestr("README.md", _README_TEXT.format(
            filename=filename,
            client_display="ci-alpha (fictional CI tenant) — TAMPERED",
            event_count=len(events),
            verifier_sha=verifier_sha,
        ))
        z.writestr(
            "TAMPER_NOTES.md",
            "# What was flipped\n\n"
            f"- Target event: seq #{target_seq} "
            f"(index {idx} of {len(events)} events, chosen mid-chain).\n"
            "- Field: `events[{idx}].payload_jcs` — one trailing ASCII space "
            "appended.\n"
            "- Effect: the recomputed `payload_jcs` from `payload` no longer "
            "matches the stored value, so verify.js exits 1 with "
            f"`FAIL — event #{target_seq}` at the payload_jcs comparison "
            "before it even gets to hash re-computation.\n"
            "- Why one space, not one bit-flip: keeps the change obvious to "
            "any auditor cracking open the zip; the failure is still a "
            "genuine chain-integrity break.\n"
        )
    return buf.getvalue(), target_seq


async def _amain() -> int:
    dsn = os.environ["DATABASE_URL"]
    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=2)
    try:
        # Idempotent — seeds the two CI tenants under fixed UUIDs.
        seed_out = await seed_ci_deterministic(pool)
        print(f"seed_ci_deterministic → tenants={[t['slug'] for t in seed_out['tenants']]}")

        t, events = await read_events(pool, CI_TENANT_A_SLUG)
        export_doc = _build_export_doc(t, events)
        # Freeze `generated_at` so a re-run produces the same bytes.
        export_doc["generated_at"] = "2024-01-01T00:00:00.000000+00:00"

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        clean_bytes = _make_bundle_bytes(export_doc, CLEAN_ZIP.name)
        CLEAN_ZIP.write_bytes(clean_bytes)
        print(f"wrote {CLEAN_ZIP.relative_to(REPO_ROOT)} "
              f"({len(clean_bytes)} bytes, {len(events)} events)")

        tampered_bytes, target_seq = _make_tampered_bytes(export_doc, TAMPERED_ZIP.name)
        TAMPERED_ZIP.write_bytes(tampered_bytes)
        print(f"wrote {TAMPERED_ZIP.relative_to(REPO_ROOT)} "
              f"({len(tampered_bytes)} bytes, target_seq=#{target_seq})")

        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(_amain()))
