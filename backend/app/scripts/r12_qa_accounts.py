"""Preview-only: create QA sign-in accounts with known password + TOTP secret (never run in production)."""
import asyncio
import os
import uuid
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
      ("qa.client@harbourline.example", "QA Client", "owner"),
      ("qa.principal@harbourline.example", "QA Principal", "principal")]
MULTI = ("qa.multi@harbourline.example", "QA Multi-tenant", "ae", ("harbourline", "ci-alpha"))  # never BurMac


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
        await af.apply_invite(store, inv, pw)
        await af.set_identity(store, email, {"mfa_secret": secret})
        print("ready", email)
    email, name, role, slugs = MULTI
    for slug in slugs:
        t = (await wrap(store).system("tenants").find_one({"slug": slug}))["tenant_id"]
        async with use_tenant(t):
            if not await wrap(store).brokerages.find_one({}):
                await wrap(store).brokerages.insert_one({"brokerage_id": str(uuid.uuid4()), "slug": slug, "display_name": slug})
            if slug != "harbourline" and not await wrap(store).clients.find_one({"display_name": "CI Alpha QA Client Pty Ltd"}):
                await wrap(store).clients.insert_one({"client_id": str(uuid.uuid4()), "display_name": "CI Alpha QA Client Pty Ltd",
                                                      "domicile": "AU", "industry": "QA", "jurisdiction_code": "AU-NSW"})
        _, inv = await af.create_invite(store, tenant_id=t, email=email, name=name, role=role, client_id=None, created_by="qa_seed")
        await af.use_token(store, inv)
        await af.apply_invite(store, inv, pw, await af.identity(store, email) if slug != slugs[0] else None)
    await af.set_identity(store, email, {"mfa_secret": secret})
    print("ready", email, "in", slugs)


if __name__ == "__main__":
    asyncio.run(main())
