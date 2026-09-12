"""CI export helper — emits the fixture-a chain to stdout as JSON."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

from app.kernel.trail import event_to_json, read_events


VERIFIER_JS = Path(__file__).resolve().parents[3] / "packages" / "kernel" / "verifier" / "verify.js"


async def main() -> None:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    dsn = os.environ["DATABASE_URL"]
    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=2)
    try:
        slug = os.environ.get("EXPORT_TENANT", "fixture-a")
        tenant, events = await read_events(pool, slug)
        export = {
            "schema_version": "trail/v1",
            "tenant": {
                "tenant_id": tenant.tenant_id, "slug": tenant.slug,
                "display_name": tenant.display_name,
                "genesis_hash": tenant.genesis_hash,
            },
            "verifier_binary_sha256": hashlib.sha256(VERIFIER_JS.read_bytes()).hexdigest() if VERIFIER_JS.exists() else "0"*64,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            "events": [event_to_json(e) for e in events],
        }
        json.dump(export, sys.stdout)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
