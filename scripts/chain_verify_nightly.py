#!/usr/bin/env python3
"""Phase-18 nightly chain-verify.

Iterates every tenant in the DB, exports each tenant's chain in
memory, runs the in-process offline verifier (same code path as
`packages/kernel/verifier/verify.js`), and writes a per-run JSON
report to `test_reports/chain_verify_<yyyy-mm-dd>.json`.

Exit code:
  0 — every scanned tenant verifies clean.
  1 — at least one tenant failed to verify.
  2 — hit the wall-time or tenant-count safety cap before finishing.

Bounded:
  - max 60s wall time (`MAX_WALL_SEC`).
  - max 100 tenants scanned (`MAX_TENANTS`) as a sanity limit.

Manual run:
  cd /app/services/api
  python /app/scripts/chain_verify_nightly.py

Cron example (2am UTC daily, environment inline; adjust DSN + PYTHONPATH):
  0 2 * * * cd /app/services/api && \
    DATABASE_URL="postgresql://brokaid:...@host:5432/brokaid" \
    PYTHONPATH=/app/services/api \
    /usr/local/bin/python /app/scripts/chain_verify_nightly.py \
    >> /var/log/brokaid/chain_verify.log 2>&1
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

# Ensure `app.*` imports resolve regardless of cwd.
_API_ROOT = Path(__file__).resolve().parents[1] / "services" / "api"
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from app.kernel.trail import read_events  # noqa: E402
from app.kernel.verifier import verify_events  # noqa: E402


MAX_WALL_SEC = 60
MAX_TENANTS = 100
REPORTS_DIR = Path("/app/test_reports")


async def _run() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("chain_verify_nightly: DATABASE_URL is not set", file=sys.stderr)
        return 1

    started = time.monotonic()
    started_utc = datetime.now(timezone.utc)

    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=2)
    per_tenant: list[dict] = []
    hit_cap = False
    try:
        async with pool.acquire() as c:
            rows = await c.fetch(
                "SELECT slug FROM tenants ORDER BY slug LIMIT $1",
                MAX_TENANTS + 1,
            )
        if len(rows) > MAX_TENANTS:
            hit_cap = True
            rows = rows[:MAX_TENANTS]

        for r in rows:
            if time.monotonic() - started > MAX_WALL_SEC:
                hit_cap = True
                break
            slug = r["slug"]
            tenant, events = await read_events(pool, slug)
            payload = [
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
            result = verify_events(payload)
            per_tenant.append({
                "slug": slug,
                "tenant_id": tenant.tenant_id,
                "n_events": result.n_events,
                "ok": result.ok,
                "root_hash": result.root_hash,
                "failure_seq": result.failure_seq,
                "failure_event_id": result.failure_event_id,
                "reason": result.reason,
            })
    finally:
        await pool.close()

    all_ok = all(t["ok"] for t in per_tenant)
    elapsed = time.monotonic() - started

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / (
        "chain_verify_" + started_utc.date().isoformat() + ".json"
    )
    report = {
        "run_started_utc": started_utc.isoformat(timespec="seconds"),
        "elapsed_sec": round(elapsed, 3),
        "tenants_scanned": len(per_tenant),
        "hit_bound": hit_cap,
        "max_wall_sec": MAX_WALL_SEC,
        "max_tenants": MAX_TENANTS,
        "all_ok": all_ok,
        "results": per_tenant,
    }
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(
        f"chain_verify_nightly: {len(per_tenant)} tenants scanned in "
        f"{elapsed:.2f}s → all_ok={all_ok} · report={report_path}"
    )

    if hit_cap and not all_ok:
        return 1
    if hit_cap:
        return 2
    return 0 if all_ok else 1


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
