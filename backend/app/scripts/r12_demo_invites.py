"""R1.2a — issue Harbourline demo-persona invites (re-runnable; each run replaces open invites).

Usage: cd /app/backend && python -m app.scripts.r12_demo_invites
Prints one single-use invite link per persona; .example addresses never receive email.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app.db import mongo as mongo_db  # noqa: E402
from app.db.tenant_safe import wrap  # noqa: E402
from app.kernel import auth_flows as af  # noqa: E402

async def main():
    await mongo_db.init_mongo_client()
    store = mongo_db.get_db_sync()
    tid = (await wrap(store).system("tenants").find_one({"slug": af.DEMO_TENANT}))["tenant_id"]
    rows = await af.issue_demo_invites(store, tid, "script",
                                       af.DEMO_PERSONAS + [("operator@harbourline.example", "operator", False)])  # + Olly
    for r in rows:
        print(f"{r['name']:<16} {r['email']:<38} {r['invite_url']}")


if __name__ == "__main__":
    asyncio.run(main())
