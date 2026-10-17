"""Preview-only: create QA sign-in accounts with known password + TOTP secret (never run in production)."""
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app.db import mongo as mongo_db  # noqa: E402
from app.db.tenant_safe import wrap  # noqa: E402
from app.kernel import auth_flows as af  # noqa: E402
from app.kernel.tenant_ctx import use_tenant  # noqa: E402
from app.seeds.seed_users import FICTIONAL_CLIENT_ID  # noqa: E402

QA = [("qa.operator@harbourline.example", "QA Operator", "operator"),
      ("qa.ae@harbourline.example", "QA Executive", "ae"),
      ("qa.client@harbourline.example", "QA Client", "owner")]


async def main():
    assert not af.is_production(), "preview only"
    await mongo_db.init_mongo_client()
    store = mongo_db.get_db_sync()
    tid = (await wrap(store).system("tenants").find_one({"slug": "harbourline"}))["tenant_id"]
    pw, secret = os.environ["QA_PASSWORD"], os.environ["QA_TOTP_SECRET"]
    for email, name, role in QA:
        _, inv = await af.create_invite(store, tenant_id=tid, email=email, name=name, role=role,
                                        client_id=str(FICTIONAL_CLIENT_ID) if role == "owner" else None, created_by="qa_seed")
        await af.use_token(store, inv)
        user = await af.apply_invite(store, inv, pw)
        async with use_tenant(tid):
            await wrap(store).users.update_one({"user_id": user["user_id"]}, {"$set": {"mfa_secret": secret, "qa": True}})
        print("ready", email)


if __name__ == "__main__":
    asyncio.run(main())
