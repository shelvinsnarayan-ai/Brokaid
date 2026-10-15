"""Seed dev-mode users, brokerage, and client links.

Idempotent. Everything is fictional. The dev password is a shared,
well-known string that must never be usable outside AUTH_DEV_MODE.
Server startup already refuses to run when AUTH_DEV_MODE=true and
ENVIRONMENT=production.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv

from app.auth import passwords
from app.kernel.trail import create_tenant

# R0.6 G3: demo tenant is Harbourline (fresh from genesis). The legacy
# tenant (slug "burmac") is left intact; its demo logins are repointed.
TENANT_SLUG = "harbourline"
TENANT_NAME = "Harbourline Insurance Brokers"
BROKERAGE_SLUG = "harbourline-brokerage"
BROKERAGE_NAME = "Harbourline Insurance Brokers Pty Ltd"
LICENSEE = "Harbourline Financial Services Pty Ltd (ACN 000-000-000)"
LEGACY_TENANT_SLUG = "burmac"
FICTIONAL_CLIENT_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
DEV_PASSWORD = "dev-password-not-for-production"


OPERATORS = [
    {"email": "principal@harbourline.example",    "display_name": "Ada Principal",   "role": "principal"},
    {"email": "deputy@harbourline.example",       "display_name": "Ben Deputy",      "role": "deputy"},
    {"email": "ae@harbourline.example",           "display_name": "Cass Executive",  "role": "ae"},
    {"email": "accounts-a@harbourline.example",   "display_name": "Dee Accounts",    "role": "accounts"},
    {"email": "accounts-b@harbourline.example",   "display_name": "Ev Accounts",     "role": "accounts"},
    {"email": "compliance@harbourline.example",   "display_name": "Fin Compliance",  "role": "compliance"},
    {"email": "operator@harbourline.example", "display_name": "Olly Operator",  "role": "operator"},
    # Data-only role example — cannot log in.
    {"email": "auditor@harbourline.example",      "display_name": "Gil Auditor",     "role": "auditor"},
]

CLIENTS = [
    {"email": "owner@fictionalclient.example",   "display_name": "Kai Owner",   "role": "client_owner"},
    {"email": "finance@fictionalclient.example", "display_name": "Sam Finance", "role": "client_finance"},
]


async def _ensure_tenant(pool: object) -> str:
    """Idempotent + concurrent-safe. Falls back to create_tenant when
    no row exists yet; concurrent racers converge on the winner's row.
    """
    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT tenant_id FROM tenants WHERE slug = $1", TENANT_SLUG,
        )
    if row:
        return str(row["tenant_id"])
    try:
        tenant, _ = await create_tenant(pool, TENANT_SLUG, TENANT_NAME)
        return tenant.tenant_id
    except Exception:
        # Lost the race — read the winner.
        async with pool.acquire() as c:
            row = await c.fetchrow(
                "SELECT tenant_id FROM tenants WHERE slug = $1", TENANT_SLUG,
            )
        assert row is not None
        return str(row["tenant_id"])


async def _ensure_brokerage(pool: object, tenant_id: str) -> str:
    async with pool.acquire() as c:
        async with c.transaction():
            # Idempotent upsert keyed on slug. Prior implementation had a
            # SELECT+INSERT race that surfaced as an FK violation on
            # `user_brokerage_roles.brokerage_id` when tests running in
            # ordering combined a TRUNCATE-CASCADE wipe with a re-seed
            # under a fresh connection. See test_seed_users_idempotent.py.
            row = await c.fetchrow(
                "INSERT INTO brokerages "
                "  (brokerage_id, tenant_id, slug, display_name, licensee) "
                "VALUES ($1::uuid, $2::uuid, $3, $4, $5) "
                "ON CONFLICT (slug) DO UPDATE "
                "  SET display_name = EXCLUDED.display_name "
                "RETURNING brokerage_id",
                str(uuid.uuid4()), tenant_id, BROKERAGE_SLUG,
                BROKERAGE_NAME, LICENSEE,
            )
    return str(row["brokerage_id"])


async def _ensure_operator(pool: object, brokerage_id: str, spec: dict) -> str:
    async with pool.acquire() as c:
        async with c.transaction():
            # Idempotent + race-safe: upsert on the email unique index.
            row = await c.fetchrow(
                "INSERT INTO users (user_id, email, display_name, kind, password_hash) "
                "VALUES ($1::uuid, $2, $3, 'operator', $4) "
                "ON CONFLICT (email) DO UPDATE "
                "  SET display_name = EXCLUDED.display_name "
                "RETURNING user_id",
                str(uuid.uuid4()), spec["email"], spec["display_name"],
                passwords.hash_password(DEV_PASSWORD),
            )
            user_id = str(row["user_id"])
            # Role — idempotent via PK (user_id, brokerage_id, role).
            await c.execute(
                "INSERT INTO user_brokerage_roles (user_id, brokerage_id, role) "
                "VALUES ($1::uuid, $2::uuid, $3) ON CONFLICT DO NOTHING",
                user_id, brokerage_id, spec["role"],
            )
    return user_id


async def _ensure_client(pool: object, tenant_id: str, spec: dict) -> str:
    async with pool.acquire() as c:
        async with c.transaction():
            row = await c.fetchrow(
                "INSERT INTO users (user_id, email, display_name, kind) "
                "VALUES ($1::uuid, $2, $3, 'client') "
                "ON CONFLICT (email) DO UPDATE "
                "  SET display_name = EXCLUDED.display_name "
                "RETURNING user_id",
                str(uuid.uuid4()), spec["email"], spec["display_name"],
            )
            user_id = str(row["user_id"])
            await c.execute(
                """
                INSERT INTO user_client_links (user_id, tenant_id, client_id, role)
                VALUES ($1::uuid, $2::uuid, $3::uuid, $4)
                ON CONFLICT DO NOTHING
                """,
                user_id, tenant_id, str(FICTIONAL_CLIENT_ID), spec["role"],
            )
    return user_id


async def seed(pool: object) -> dict:
    tenant_id = await _ensure_tenant(pool)
    brokerage_id = await _ensure_brokerage(pool, tenant_id)
    ops = []
    for s in OPERATORS:
        uid = await _ensure_operator(pool, brokerage_id, s)
        ops.append({"user_id": uid, **s})
    cls = []
    for s in CLIENTS:
        uid = await _ensure_client(pool, tenant_id, s)
        cls.append({"user_id": uid, **s})
    return {
        "tenant_id": tenant_id,
        "brokerage_id": brokerage_id,
        "client_entity_id": str(FICTIONAL_CLIENT_ID),
        "operators": ops,
        "clients": cls,
    }


async def _main() -> int:
    # TODO(R6): remove — dead since R5c-4a PG removal. The `await None`
    # below is syntactically broken; nothing invokes this entrypoint.
    # Left as an F841 canary + explicit marker for the next cleanup pass.
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    dsn = os.environ["MONGO_URL"]
    pool = await None  # PG deleted R5c-4a(dsn=dsn, min_size=1, max_size=2)
    try:
        result = await seed(pool)
        print(
            f"Seeded tenant={result['tenant_id']} brokerage={result['brokerage_id']}\n"
            f"  operators: {len(result['operators'])}\n"
            f"  clients:   {len(result['clients'])}"
        )
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(_main()))
