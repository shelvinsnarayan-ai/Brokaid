"""One-off: seed the CI deterministic tenants + one operator + one
client per tenant into the live preview Mongo, so the E2E tester
can prove cross-tenant isolation at the API layer.

Emails registered (dev-mode `AUTH_DEV_MODE=true` impersonation):
  ci-alpha:
    - operator: principal@ci-alpha.example (role=principal)
    - client:   owner@ci-alpha.example
  ci-beta:
    - operator: principal@ci-beta.example  (role=principal)
    - client:   owner@ci-beta.example

Idempotent — run twice; the second run is a no-op.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
load_dotenv(Path(__file__).resolve().parents[1] / "backend" / ".env")

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from pymongo.errors import DuplicateKeyError  # noqa: E402

from app.auth import passwords  # noqa: E402
from app.db.tenant_safe import wrap  # noqa: E402
from app.kernel.tenant_ctx import use_tenant  # noqa: E402
from app.kernel.trail_mongo import DuplicateIdempotencyKey  # noqa: E402
from app.seeds.ci_deterministic import (  # noqa: E402
    CI_TENANT_A_ID, CI_TENANT_A_SLUG,
    CI_TENANT_B_ID, CI_TENANT_B_SLUG,
    seed_ci_deterministic_mongo,
)

DEV_PASSWORD = "brokaid-dev-password-only"


async def _ensure_operator(safe_db, tenant_id, email, display_name):
    async with use_tenant(tenant_id):
        existing = await safe_db.users.find_one({"email": email})
        if existing:
            user_id = existing["user_id"]
        else:
            user_id = str(uuid.uuid4())
            try:
                await safe_db.users.insert_one({
                    "user_id": user_id, "email": email,
                    "display_name": display_name, "kind": "operator",
                    "password_hash": passwords.hash_password(DEV_PASSWORD),
                    "mfa_secret": None,
                    "created_at": datetime.now(timezone.utc),
                })
            except (DuplicateKeyError, DuplicateIdempotencyKey):
                existing = await safe_db.users.find_one({"email": email})
                user_id = existing["user_id"]
        # Fabricate a synthetic brokerage_id so the tester can auth in
        # — CI tenants have no real BrokerageCreated event. `principal`
        # is a `LOGIN_CAPABLE_ROLES` member so `require_operator_session`
        # admits the session.
        brokerage_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL, f"ci-brokerage:{tenant_id}",
        ))
        try:
            await safe_db.user_brokerage_roles.insert_one({
                "user_id": user_id, "tenant_id": tenant_id,
                "brokerage_id": brokerage_id, "role": "principal",
                "active": True,
            })
        except (DuplicateKeyError, DuplicateIdempotencyKey):
            pass
        return user_id


async def _ensure_client(safe_db, tenant_id, slug, email, display_name):
    async with use_tenant(tenant_id):
        existing = await safe_db.users.find_one({"email": email})
        if existing:
            user_id = existing["user_id"]
        else:
            user_id = str(uuid.uuid4())
            try:
                await safe_db.users.insert_one({
                    "user_id": user_id, "email": email,
                    "display_name": display_name, "kind": "client",
                    "password_hash": None, "mfa_secret": None,
                    "created_at": datetime.now(timezone.utc),
                })
            except (DuplicateKeyError, DuplicateIdempotencyKey):
                existing = await safe_db.users.find_one({"email": email})
                user_id = existing["user_id"]
        # Link user → the ci-deterministic Acme client entity (see
        # `ci_deterministic._build_event_specs`: client_id is derived
        # via `_id(f"{slug}:client:acme")`).
        from app.seeds.ci_deterministic import _id as ci_id
        client_id = ci_id(f"{slug}:client:acme")
        try:
            await safe_db.user_client_links.insert_one({
                "user_id": user_id, "tenant_id": tenant_id,
                "client_id": client_id, "role": "client_owner",
                "active": True,
            })
        except (DuplicateKeyError, DuplicateIdempotencyKey):
            pass
        return user_id, client_id


async def _run() -> int:
    raw = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017")
    url = raw.replace("://localhost:", "://127.0.0.1:") if "localhost" in raw else raw
    dbn = os.environ.get("DB_NAME", "brokaid")

    client = AsyncIOMotorClient(url, uuidRepresentation="standard")
    try:
        db = client[dbn]
        # 1. Deterministic events for both CI tenants.
        summary = await seed_ci_deterministic_mongo(db)
        print("CI tenants seeded:")
        for t in summary["tenants"]:
            print(f"  {t['slug']}  tenant_id={t['tenant_id']}  "
                  f"genesis={t['genesis_hash'][:16]}...")

        safe_db = wrap(db)

        # 2. Register operator + client per CI tenant.
        registered: list[dict] = []
        for tid, slug, nice in (
            (CI_TENANT_A_ID, CI_TENANT_A_SLUG, "CI Alpha"),
            (CI_TENANT_B_ID, CI_TENANT_B_SLUG, "CI Beta"),
        ):
            op_email = f"principal@{slug}.example"
            cl_email = f"owner@{slug}.example"
            op_id = await _ensure_operator(
                safe_db, tid, op_email, f"{nice} Principal",
            )
            cl_id, client_ent_id = await _ensure_client(
                safe_db, tid, slug, cl_email, f"{nice} Client Owner",
            )
            registered.append({
                "slug": slug, "tenant_id": tid,
                "operator_email": op_email, "operator_user_id": op_id,
                "client_email": cl_email, "client_user_id": cl_id,
                "client_entity_id": client_ent_id,
            })

        print("Users registered:")
        for r in registered:
            print(f"  [{r['slug']} tid={r['tenant_id'][:8]}...]")
            print(f"    operator: {r['operator_email']}")
            print(f"    client:   {r['client_email']}"
                  f"  (client_entity_id={r['client_entity_id'][:8]}...)")

        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
