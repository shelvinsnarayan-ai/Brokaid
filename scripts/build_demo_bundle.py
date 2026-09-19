"""Phase R5c-3b — build the pre-signed offline verifier bundles for SIRC (Mongo-native).

Emits two artefacts under docs/demo_bundle/:
  * ci-alpha.zip           — clean bundle, verifies OK
  * ci-alpha-tampered.zip  — one byte flipped mid-chain, verifies FAIL

R5c-3b: seed + read the ledger via the Mongo-primary path. Zero
asyncpg or pool references. Uses `seed_ci_deterministic_mongo(db)`
and `trail_dispatch.read_events(db, slug)`. Byte-identical hash
chain to the archived PG-generated bundle (root `92e61063...`)
because the seed sequence is shared verbatim across backends
(`_build_event_specs`).

Determinism controls:
  * A scratch Mongo database is used (`DB_NAME` env override:
    `brokaid_demo_bundle`) so per-tenant seq numbers start clean
    at 1 on every run.
  * `_build_export_doc.generated_at` is frozen at
    `2024-01-01T00:00:00.000000+00:00`.
  * Two consecutive runs produce byte-identical zips.

Runs standalone: `python scripts/build_demo_bundle.py`.

Fictional data only.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import zipfile
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

# ---- Wire up the API package ---------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))
load_dotenv(BACKEND_ROOT / ".env")

# Scratch DB so per-tenant seq starts at 1 and every run is deterministic.
os.environ["DB_NAME"] = os.environ.get(
    "BROKAID_DEMO_BUNDLE_DB", "brokaid_demo_bundle",
)

from app.kernel.trail_dispatch import read_events  # noqa: E402
from app.routes.trail import (  # noqa: E402
    VERIFIER_JS,
    _build_export_doc,
    _README_TEXT,
    _verifier_sha256,
)
from app.seeds.ci_deterministic import (  # noqa: E402
    CI_TENANT_A_SLUG,
    seed_ci_deterministic_mongo,
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


def _make_tampered_bytes(
    export_doc: dict, filename: str,
) -> tuple[bytes, int]:
    """Flip one byte in a mid-chain event's payload_jcs."""
    doc = json.loads(json.dumps(export_doc))  # deep copy
    events = doc["events"]
    if not events:
        raise RuntimeError("ci-alpha has no events; cannot tamper")
    idx = len(events) // 2
    target_seq = events[idx]["seq"]
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
            "- Field: `events[{idx}].payload_jcs` — one trailing ASCII "
            "space appended.\n"
            "- Effect: the recomputed `payload_jcs` from `payload` no "
            "longer matches the stored value, so verify.js exits 1 "
            f"with `FAIL — event #{target_seq}` at the payload_jcs "
            "comparison before it even gets to hash re-computation.\n"
            "- Why one space, not one bit-flip: keeps the change "
            "obvious to any auditor cracking open the zip; the "
            "failure is still a genuine chain-integrity break.\n"
        )
    return buf.getvalue(), target_seq


async def build_bundles() -> dict:
    """Idempotent bundle build. Returns
    `{clean_bytes, tampered_bytes, target_seq, root_hash, n_events}`."""
    # Prefer explicit 127.0.0.1 when MONGO_URL points at localhost —
    # the container's `localhost` sometimes resolves to IPv6 (::1) but
    # mongod binds IPv4 only, so a plain `localhost` MongoClient
    # times out from a fresh interpreter.
    raw = os.environ["MONGO_URL"]
    mongo_url = raw.replace(
        "://localhost:", "://127.0.0.1:",
    ) if "localhost" in raw else raw
    db_name = os.environ["DB_NAME"]
    client = AsyncIOMotorClient(mongo_url, uuidRepresentation="standard")
    try:
        # Fresh DB every build so per-tenant seq starts at 1 and each
        # run is byte-identical to the previous.
        await client.drop_database(db_name)
        db = client[db_name]

        seed_out = await seed_ci_deterministic_mongo(db)
        slugs = [t["slug"] for t in seed_out["tenants"]]
        print(f"seed_ci_deterministic_mongo → tenants={slugs}")

        t, events = await read_events(db, CI_TENANT_A_SLUG)
        export_doc = _build_export_doc(t, events)
        # Freeze `generated_at` so a re-run produces the same bytes.
        export_doc["generated_at"] = "2024-01-01T00:00:00.000000+00:00"

        clean_bytes = _make_bundle_bytes(export_doc, CLEAN_ZIP.name)
        tampered_bytes, target_seq = _make_tampered_bytes(
            export_doc, TAMPERED_ZIP.name,
        )
        return {
            "clean_bytes": clean_bytes,
            "tampered_bytes": tampered_bytes,
            "target_seq": target_seq,
            "root_hash": (events[-1].event_hash if events else None),
            "n_events": len(export_doc["events"]),
        }
    finally:
        client.close()


async def _amain() -> int:
    result = await build_bundles()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CLEAN_ZIP.write_bytes(result["clean_bytes"])
    print(
        f"wrote {CLEAN_ZIP.relative_to(REPO_ROOT)} "
        f"({len(result['clean_bytes'])} bytes, "
        f"{result['n_events']} events, root={result['root_hash']})"
    )
    TAMPERED_ZIP.write_bytes(result["tampered_bytes"])
    print(
        f"wrote {TAMPERED_ZIP.relative_to(REPO_ROOT)} "
        f"({len(result['tampered_bytes'])} bytes, "
        f"target_seq=#{result['target_seq']})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_amain()))
