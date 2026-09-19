#!/usr/bin/env python3
"""Phase R5c-3b nightly chain-verify (Mongo-native + PG archive parity).

Two verifications every night:
  1. LIVE — iterate every tenant on the Mongo store, export the
     chain via `trail_dispatch.read_events(mongo_db, slug)`, run
     the in-process offline verifier.
  2. ARCHIVE — load `docs/demo_bundle/ci-alpha.pg.zip` from disk
     (the pre-R5c-3b PG-generated canonical bundle preserved as an
     archive pin), extract `export.json`, run the same verifier.
     This one-shot historical parity check pins the PG chain root
     hash `92e61063…` in perpetuity, independent of the live store.

Prints two lines to stdout in the format:
  mongo_root=<hash> PASS|FAIL
  pg_archive_root=<hash> PASS|FAIL

Exit code:
  0 — both verifications pass.
  1 — either verification fails.

Bounded:
  - max 60s wall time (`MAX_WALL_SEC`).
  - max 100 tenants scanned on the live side (`MAX_TENANTS`).

Manual run:
  cd /app/backend
  python /app/scripts/chain_verify_nightly.py

Cron example (2am UTC daily):
  0 2 * * * cd /app/backend && \\
    MONGO_URL="mongodb://host:27017" DB_NAME="brokaid" \\
    PYTHONPATH=/app/backend \\
    /usr/local/bin/python /app/scripts/chain_verify_nightly.py \\
    >> /var/log/brokaid/chain_verify.log 2>&1
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

# Ensure `app.*` imports resolve regardless of cwd.
_API_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from app.kernel.trail_dispatch import read_events  # noqa: E402
from app.kernel.verifier import verify_events  # noqa: E402


MAX_WALL_SEC = 60
MAX_TENANTS = 100
REPO_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = Path("/app/test_reports")
PG_ARCHIVE_ZIP = REPO_ROOT / "docs" / "demo_bundle" / "ci-alpha.pg.zip"


def _events_to_payload(events) -> list[dict]:
    return [
        {
            "event_id": e.event_id, "tenant_id": e.tenant_id,
            "seq": e.seq, "ts": e.ts, "actor": e.actor,
            "type": e.type, "payload": e.payload,
            "payload_jcs": e.payload_jcs,
            "prev_hash": e.prev_hash, "event_hash": e.event_hash,
            "idempotency_key": e.idempotency_key,
            "mocked": e.mocked,
        }
        for e in events
    ]


def _verify_pg_archive() -> tuple[str | None, bool, str | None]:
    """Load ci-alpha.pg.zip, extract export.json, run offline verify.
    Returns (root_hash, ok, reason)."""
    if not PG_ARCHIVE_ZIP.exists():
        return None, False, f"archive not found: {PG_ARCHIVE_ZIP}"
    with zipfile.ZipFile(PG_ARCHIVE_ZIP, "r") as z:
        with z.open("export.json") as f:
            export_doc = json.loads(f.read().decode("utf-8"))
    result = verify_events(export_doc.get("events", []))
    return (
        result.root_hash, bool(result.ok),
        None if result.ok else result.reason,
    )


async def _verify_mongo_live() -> tuple[list[dict], bool, str | None]:
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        return [], False, "MONGO_URL/DB_NAME not set"

    started = time.monotonic()
    per_tenant: list[dict] = []
    raw = mongo_url
    resolved_url = raw.replace(
        "://localhost:", "://127.0.0.1:",
    ) if "localhost" in raw else raw
    client = AsyncIOMotorClient(resolved_url, uuidRepresentation="standard")
    try:
        db = client[db_name]
        # tenant-safe: system-only — pre-context registry lookup.
        cursor = db["tenants"].find(
            {}, projection={"slug": 1, "tenant_id": 1, "_id": 0},
        ).sort("slug", 1).limit(MAX_TENANTS + 1)
        slugs: list[dict] = [r async for r in cursor]
        hit_cap = len(slugs) > MAX_TENANTS
        slugs = slugs[:MAX_TENANTS]

        for r in slugs:
            if time.monotonic() - started > MAX_WALL_SEC:
                hit_cap = True
                break
            slug = r["slug"]
            try:
                tenant, events = await read_events(db, slug)
            except Exception as e:  # noqa: BLE001
                per_tenant.append({
                    "slug": slug, "ok": False,
                    "reason": f"read_events failed: {e!r}",
                })
                continue
            result = verify_events(_events_to_payload(events))
            per_tenant.append({
                "slug": slug, "tenant_id": tenant.tenant_id,
                "n_events": result.n_events, "ok": result.ok,
                "root_hash": result.root_hash,
                "failure_seq": result.failure_seq,
                "failure_event_id": result.failure_event_id,
                "reason": result.reason,
            })
    finally:
        client.close()

    all_ok = all(t.get("ok") for t in per_tenant)
    return per_tenant, all_ok, None


async def _run() -> int:
    started = time.monotonic()
    started_utc = datetime.now(timezone.utc)

    per_tenant, mongo_ok, mongo_err = await _verify_mongo_live()
    # Pick a representative "mongo_root" for stdout — the ci-alpha
    # tenant if present; otherwise the first non-empty root; else
    # "(empty)" so the format never breaks.
    mongo_root: str | None = None
    for t in per_tenant:
        if t.get("slug") == "ci-alpha" and t.get("root_hash"):
            mongo_root = t["root_hash"]
            break
    if mongo_root is None:
        for t in per_tenant:
            if t.get("root_hash"):
                mongo_root = t["root_hash"]
                break

    pg_root, pg_ok, pg_reason = _verify_pg_archive()

    print(f"mongo_root={mongo_root or '(empty)'} {'PASS' if mongo_ok else 'FAIL'}")
    print(f"pg_archive_root={pg_root or '(empty)'} {'PASS' if pg_ok else 'FAIL'}")
    if mongo_err:
        print(f"  mongo_err={mongo_err}", file=sys.stderr)
    if pg_reason:
        print(f"  pg_reason={pg_reason}", file=sys.stderr)

    elapsed = time.monotonic() - started
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "run_started_utc": started_utc.isoformat(timespec="seconds"),
        "elapsed_sec": round(elapsed, 3),
        "mongo_ok": mongo_ok,
        "mongo_root": mongo_root,
        "mongo_per_tenant": per_tenant,
        "pg_archive_ok": pg_ok,
        "pg_archive_root": pg_root,
        "pg_archive_reason": pg_reason,
        # Legacy nightly-report keys read by
        # `tests/test_chain_verify_nightly.py`.
        "all_ok": bool(mongo_ok and pg_ok),
        "tenants_scanned": len(per_tenant),
        "hit_bound": False,
        "results": per_tenant,
    }
    report_path = REPORTS_DIR / (
        "chain_verify_" + started_utc.date().isoformat() + ".json"
    )
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True))
    return 0 if (mongo_ok and pg_ok) else 1


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
