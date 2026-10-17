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
from app.kernel.tenant_ctx import use_tenant  # noqa: E402
from app.seeds.seed_users import FICTIONAL_CLIENT_ID  # noqa: E402

PERSONAS = [  # (email, role, is_client)
    ("owner@fictionalclient.example", "owner", True),        # Kai
    ("bookkeeper@fictionalclient.example", "bookkeeper", True),  # Bea
    ("ae@harbourline.example", "ae", False),                 # Cass
    ("principal@harbourline.example", "principal", False),   # Ada
    ("operator@harbourline.example", "operator", False),     # Olly
]


async def main() -> list[tuple[str, str, str]]:
    await mongo_db.init_mongo_client()
    store = mongo_db.get_db_sync()
    tenant = await wrap(store).system("tenants").find_one({"slug": "harbourline"})
    tid, out = tenant["tenant_id"], []
    for email, role, is_client in PERSONAS:
        user, _ = await af.find_user(store, email)
        client_id = None
        if is_client:
            async with use_tenant(tid):
                link = await wrap(store).user_client_links.find_one({"user_id": user["user_id"]}) if user else None
            client_id = link["client_id"] if link else str(FICTIONAL_CLIENT_ID)
        token, doc = await af.create_invite(store, tenant_id=tid, email=email, name=(user or {}).get("display_name", ""),
                                            role=role, client_id=client_id, created_by="r12_demo_invites")
        await af.ledger(store, tid, "InviteCreated", {"kind": "system", "id": "r12_demo_invites", "name": "script"},
                        {"invite_id": doc["token_id"], "role": role, "client_id": client_id, "email_status": "skipped"})
        out.append((doc["name"], email, af.invite_url(token)))
    for name, email, url in out:
        print(f"{name:<16} {email:<38} {url}")
    return out


if __name__ == "__main__":
    asyncio.run(main())
