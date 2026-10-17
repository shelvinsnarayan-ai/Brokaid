"""R1.6 acceptance on preview: a full renewal of one imported policy in the `import-test` tenant (injected clock).

Usage: cd backend && python -m app.scripts.r16_import_test_renewal
Creates QA owner/bookkeeper/AE/Principal identities for one imported client in the test tenant only.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
import httpx  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from app.db.tenant_safe import wrap  # noqa: E402
from app.kernel import renewals as rn  # noqa: E402
from app.kernel.tenant_ctx import use_tenant  # noqa: E402


async def main() -> None:
    from tests.test_r16_renewals import _full_renewal, _people
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    t = await db.tenants.find_one({"slug": "import-test"})  # tenant-safe: system-only
    assert t and t.get("test"), "import-test tenant missing (run r14_import_test_tenant)"
    tid = t["tenant_id"]
    async with use_tenant(tid):
        done = {r["policy_id"] async for r in wrap(db).renewals.find({}, {"policy_id": 1})}
        pol = await wrap(db).policies.find_one({"status": "managed", "imported_from": {"$ne": None}, "expiry": {"$gt": datetime.now(timezone.utc)},
                                                "policy_id": {"$nin": list(done)}, "schedule.excess.any_one_occurrence": {"$ne": None}})
    ids, ck = await _people(db, tid, pol["client_id"])
    names = (await rn.insurers(db, tid))[:3]
    base = Path("/app/frontend/.env").read_text().split("REACT_APP_BACKEND_URL=")[1].split()[0]
    async with httpx.AsyncClient(base_url=base, timeout=60) as http:
        r, new = await _full_renewal(db, http, tid, pol["policy_id"], ids, ck,
                                     {"claims": {"any": False}, "turnover": {"value": "640000"}, "changes": {"text": ""}}, names)
    print(f"import-test: policy {pol['policy_id'][:8]} ({pol.get('product')}, iAdviser {(pol.get('external') or {}).get('iadviser_policy_number')}) "
          f"→ bound as {new['policy_id'][:8]} {new['inception']:%d/%m/%Y}–{new['expiry']:%d/%m/%Y} with {new['insurer_display']}; "
          f"stages {[h['to'] for h in r['history']]}")


if __name__ == "__main__":
    asyncio.run(main())
