"""Phase 17 follow-up — seed Burmac queue + return envelope IDs (RLS-aware)."""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "api"))
import asyncpg
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / "services" / "api" / ".env")

BURMAC = "e73e7029-da66-4659-9ac8-845f22b1f8a5"


async def _query(pool):
    async with pool.acquire() as c:
        await c.execute("SELECT set_config('app.tenant_id', $1, false)", BURMAC)
        return await c.fetch(
            "SELECT envelope_id::text AS eid, class_key, resolved_at IS NULL AS pending "
            "FROM proj_queue WHERE tenant_id=$1::uuid ORDER BY class_key",
            BURMAC,
        )


async def main() -> int:
    dsn = os.environ["DATABASE_URL"]
    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=2)
    try:
        rows = await _query(pool)
        pending = [r for r in rows if r["pending"]]
        print(f"pre-seed: {len(rows)} rows total, {len(pending)} pending")

        if len(pending) < 2:
            from app.seeds.seed_all import seed_all
            out = await seed_all(pool)
            print(f"seed_all → queue_cards={out.get('queue_cards')}")

        rows = await _query(pool)
        by_class = {r["class_key"]: r["eid"] for r in rows if r["pending"]}
        print("pending queue by class_key:")
        for k, v in by_class.items():
            print(f"  {k}: {v}")

        placement = by_class.get("submit_to_market")
        endorsement = by_class.get("endorsement_execute_instructed_quoted")

        out = {
            "placement_envelope_id": placement,
            "endorsement_envelope_id": endorsement,
        }
        Path("/tmp/burmac_envelopes.json").write_text(json.dumps(out, indent=2))
        print(f"\nwrote /tmp/burmac_envelopes.json:\n{json.dumps(out, indent=2)}")
        return 0 if (placement and endorsement) else 2
    finally:
        await pool.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
