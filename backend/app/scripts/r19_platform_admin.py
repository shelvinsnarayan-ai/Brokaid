"""Preview-only: a platform_admin QA account (known password + TOTP) in the platform tenant. Never run in production.

Usage: QA_PASSWORD=... QA_TOTP_SECRET=... python -m app.scripts.r19_platform_admin   (email must be in PLATFORM_ADMIN_EMAILS)
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
from app.db import mongo as mongo_db  # noqa: E402
from app.db.tenant_safe import wrap  # noqa: E402
from app.kernel import auth_flows as af, platform  # noqa: E402
from app.kernel.tenant_ctx import use_tenant  # noqa: E402

EMAIL = "qa.platform@harbourline.example"


async def main():
    assert not af.is_production(), "preview only"
    assert EMAIL in platform.admin_emails(), "add the QA email to PLATFORM_ADMIN_EMAILS"
    await mongo_db.init_mongo_client()
    store = mongo_db.get_db_sync()
    await platform.bootstrap(store, send=lambda **kw: asyncio.sleep(0))
    tid = (await platform.platform_tenant(store))["tenant_id"]
    async with use_tenant(tid):
        exists = await wrap(store).users.find_one({"email": EMAIL})
    if not exists:
        _, inv = await af.create_invite(store, tenant_id=tid, email=EMAIL, name="QA Platform Admin", role="platform_admin",
                                        client_id=None, created_by="bootstrap", bootstrap=True)
        await af.use_token(store, inv)
        await af.apply_invite(store, inv, os.environ["QA_PASSWORD"], await af.identity(store, EMAIL))
    async with use_tenant(tid):
        safe = wrap(store)
        u = await safe.users.find_one({"email": EMAIL})
        b = await safe.brokerages.find_one({})
        await safe.user_brokerage_roles.update_one({"user_id": u["user_id"], "role": "platform_admin"},
                                                   {"$set": {"active": True, "brokerage_id": b["brokerage_id"]}}, upsert=True)
    await af.set_identity(store, EMAIL, {"mfa_secret": os.environ["QA_TOTP_SECRET"]})
    print("ready", EMAIL, "platform tenant", tid)


if __name__ == "__main__":
    asyncio.run(main())
