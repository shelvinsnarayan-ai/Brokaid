"""Preview-only: a TEST tenant ("import-test") for trying the iAdviser importer and clean start safely.

qa.operator + qa.principal get roles there (same identity/password/TOTP as on Harbourline). Never run in production.
"""
import asyncio
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app.db import mongo as mongo_db  # noqa: E402
from app.db.tenant_safe import wrap  # noqa: E402
from app.kernel import auth_flows as af  # noqa: E402
from app.kernel import trail_mongo  # noqa: E402
from app.kernel.tenant_ctx import use_tenant  # noqa: E402

SLUG = "import-test"
PEOPLE = (("qa.operator@harbourline.example", "QA Operator", "operator"), ("qa.principal@harbourline.example", "QA Principal", "principal"))


async def main():
    assert not af.is_production(), "preview only"
    await mongo_db.init_mongo_client()
    store = mongo_db.get_db_sync()
    safe = wrap(store)
    t = await safe.system("tenants").find_one({"slug": SLUG})
    tid = t["tenant_id"] if t else str(uuid.uuid4())
    async with use_tenant(tid):
        if not t:
            await trail_mongo.create_tenant(safe, slug=SLUG, display_name="Import Test Brokers (test tenant)", tenant_id_override=tid)
        await safe.system("tenants").update_one({"tenant_id": tid}, {"$set": {"test": True}})
        if not await safe.brokerages.find_one({}):
            await safe.brokerages.insert_one({"brokerage_id": str(uuid.uuid4()), "slug": SLUG, "display_name": "Import Test Brokers"})
        if not await safe.users.find_one({"name": "Tess Account"}):
            await safe.users.insert_one({"user_id": str(uuid.uuid4()), "kind": "operator", "name": "Tess Account",
                                         "email": "tess.account@importtest.example", "role": "ae"})
        for i in range(5):  # legacy synthetic rows so the clean-start dry run has something to show
            if not await safe.clients.find_one({"client_id": f"legacy-{i}"}):
                await safe.clients.insert_one({"client_id": f"legacy-{i}", "display_name": f"Legacy Demo Client {i}", "archived_at": None})
    for email, name, role in PEOPLE:
        async with use_tenant(tid):
            if await safe.users.find_one({"email": email}):
                continue
        _, inv = await af.create_invite(store, tenant_id=tid, email=email, name=name, role=role, client_id=None, created_by="qa_seed")
        await af.use_token(store, inv)
        await af.apply_invite(store, inv, None, await af.identity(store, email))
    print("ready", SLUG, tid)


if __name__ == "__main__":
    asyncio.run(main())
