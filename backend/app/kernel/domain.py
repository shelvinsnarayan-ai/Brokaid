"""Phase R4b — domain-layer dispatcher (clients + policies).

R4b covers the `/api/o/queue` flow's minimum: clients + policies.
Claims, brokerages, verifier_tokens, magic_link_tokens are R4c.
"""
from __future__ import annotations

import os

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.tenant_safe import TenantSafeDatabase
from app.kernel import domain_mongo


class DomainBackendMismatch(RuntimeError):
    pass


def active_backend() -> str:
    return os.environ.get("BROKAID_DOMAIN", "mongo").lower()


def _is_mongo(store) -> bool:
    return isinstance(store, (AsyncIOMotorDatabase, TenantSafeDatabase))


def _is_pg(store) -> bool:
    return isinstance(store, object)


# ---- Postgres adapter -------------------------------------------------
class _PgDomain:
    async def create_client(self, pool, *, client_id, display_name,
                             jurisdiction=None, vulnerable_flag=False):
        async with pool.acquire() as c:
            async with c.transaction():
                # Caller is expected to have set app.tenant_id already
                # in most existing paths. For R4b's parity tests we
                # accept a tenant_id kwarg explicitly.
                raise NotImplementedError(
                    "R4b Postgres client creation via dispatcher not "
                    "implemented — existing routes use inline SQL. This "
                    "dispatcher only supports the Mongo write path today."
                )

    async def find_client(self, pool, client_id):
        async with pool.acquire() as c:
            row = await c.fetchrow(
                "SELECT client_id::text AS client_id, "
                "       tenant_id::text AS tenant_id, "
                "       display_name, jurisdiction, "
                "       COALESCE(vulnerable_flag, FALSE) AS vulnerable_flag "
                "FROM clients WHERE client_id = $1::uuid", client_id,
            )
        if not row:
            return None
        return domain_mongo.Client(
            client_id=row["client_id"], tenant_id=row["tenant_id"],
            display_name=row["display_name"],
            jurisdiction=row["jurisdiction"],
            vulnerable_flag=bool(row["vulnerable_flag"]),
        )

    async def find_policy(self, pool, policy_id):
        async with pool.acquire() as c:
            row = await c.fetchrow(
                "SELECT policy_id::text AS policy_id, "
                "       tenant_id::text AS tenant_id, "
                "       client_id::text AS client_id, "
                "       product, status "
                "FROM policies WHERE policy_id = $1::uuid", policy_id,
            )
        if not row:
            return None
        return domain_mongo.Policy(**dict(row))


_pg_impl = _PgDomain()


# ---- Public dispatcher functions -------------------------------------
async def create_client(store, *, tenant_id, client_id, display_name,
                         jurisdiction=None, vulnerable_flag=False):
    if _is_mongo(store):
        from app.kernel.tenant_ctx import use_tenant
        async with use_tenant(tenant_id):
            return await domain_mongo.create_client(
                store, client_id=client_id, display_name=display_name,
                jurisdiction=jurisdiction, vulnerable_flag=vulnerable_flag,
            )
    raise DomainBackendMismatch(
        "R4b Postgres create_client via dispatcher not implemented — "
        "existing seeds use inline SQL"
    )


async def find_client(store, client_id):
    if _is_mongo(store):
        return await domain_mongo.find_client(store, client_id)
    if _is_pg(store):
        return await _pg_impl.find_client(store, client_id)
    raise DomainBackendMismatch(f"unknown store type: {type(store).__name__}")


async def create_policy(store, *, tenant_id, policy_id, client_id, product,
                         status="active"):
    if _is_mongo(store):
        from app.kernel.tenant_ctx import use_tenant
        async with use_tenant(tenant_id):
            return await domain_mongo.create_policy(
                store, policy_id=policy_id, client_id=client_id,
                product=product, status=status,
            )
    raise DomainBackendMismatch(
        "R4b Postgres create_policy via dispatcher not implemented — "
        "existing seeds use inline SQL"
    )


async def find_policy(store, policy_id):
    if _is_mongo(store):
        return await domain_mongo.find_policy(store, policy_id)
    if _is_pg(store):
        return await _pg_impl.find_policy(store, policy_id)
    raise DomainBackendMismatch(f"unknown store type: {type(store).__name__}")


async def list_policies_for_client(store, tenant_id, client_id):
    if _is_mongo(store):
        from app.kernel.tenant_ctx import use_tenant
        async with use_tenant(tenant_id):
            return await domain_mongo.list_policies_for_client(store, client_id)
    raise DomainBackendMismatch(
        "R4b Postgres list_policies_for_client via dispatcher not implemented"
    )


__all__ = [
    "DomainBackendMismatch",
    "active_backend",
    "create_client", "find_client",
    "create_policy", "find_policy", "list_policies_for_client",
]
