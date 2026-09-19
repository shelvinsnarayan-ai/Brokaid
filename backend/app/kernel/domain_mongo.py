"""Phase R4b — Mongo backend for the domain layer.

Covers the collections needed by the `/api/o/queue` scenario:
  clients   — client entities (id, display_name, jurisdiction, vulnerable_flag)
  policies  — issued policies (id, client_id, product, status)

Other domain collections (claims, brokerages, verifier_tokens,
magic_link_tokens) are R4c scope — kept on Postgres until then.

All ops go through `TenantSafeDatabase`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

from app.db.tenant_safe import TenantSafeDatabase, wrap
from app.kernel.tenant_ctx import get_tenant_id


_indexes_initialised: set[int] = set()


async def _ensure_indexes(safe_db: TenantSafeDatabase) -> None:
    raw = safe_db.raw
    key = id(raw)
    if key in _indexes_initialised:
        return
    await raw["clients"].create_index(
        [("tenant_id", ASCENDING), ("client_id", ASCENDING)],
        unique=True, name="clients_tenant_id_uniq",
    )
    await raw["policies"].create_index(
        [("tenant_id", ASCENDING), ("policy_id", ASCENDING)],
        unique=True, name="policies_tenant_id_uniq",
    )
    await raw["policies"].create_index(
        [("tenant_id", ASCENDING), ("client_id", ASCENDING)],
        name="policies_tenant_client",
    )
    _indexes_initialised.add(key)


# ---- Clients ----------------------------------------------------------
@dataclass(frozen=True)
class Client:
    client_id: str
    tenant_id: str
    display_name: str
    jurisdiction: Optional[str]
    vulnerable_flag: bool


async def create_client(
    db, *, client_id: str, display_name: str,
    jurisdiction: Optional[str] = None, vulnerable_flag: bool = False,
) -> Client:
    safe_db = wrap(db)
    await _ensure_indexes(safe_db)
    tenant_id = get_tenant_id()
    try:
        await safe_db.clients.insert_one({
            "client_id": client_id, "tenant_id": tenant_id,
            "display_name": display_name, "jurisdiction": jurisdiction,
            "vulnerable_flag": bool(vulnerable_flag),
            "created_at": datetime.now(timezone.utc),
        })
    except DuplicateKeyError:
        pass
    return Client(
        client_id=client_id, tenant_id=tenant_id,
        display_name=display_name, jurisdiction=jurisdiction,
        vulnerable_flag=bool(vulnerable_flag),
    )


async def find_client(db, client_id: str) -> Optional[Client]:
    safe_db = wrap(db)
    await _ensure_indexes(safe_db)
    r = await safe_db.clients.find_one({"client_id": client_id})
    if r is None:
        return None
    return Client(
        client_id=r["client_id"], tenant_id=r["tenant_id"],
        display_name=r["display_name"],
        jurisdiction=r.get("jurisdiction"),
        vulnerable_flag=bool(r.get("vulnerable_flag", False)),
    )


async def list_clients(db) -> list[Client]:
    safe_db = wrap(db)
    await _ensure_indexes(safe_db)
    get_tenant_id()  # fail-loud on missing ctx
    out = []
    async for r in safe_db.clients.find({}).sort("display_name", ASCENDING):
        out.append(Client(
            client_id=r["client_id"], tenant_id=r["tenant_id"],
            display_name=r["display_name"],
            jurisdiction=r.get("jurisdiction"),
            vulnerable_flag=bool(r.get("vulnerable_flag", False)),
        ))
    return out


# ---- Policies ---------------------------------------------------------
@dataclass(frozen=True)
class Policy:
    policy_id: str
    tenant_id: str
    client_id: str
    product: str
    status: str


async def create_policy(
    db, *, policy_id: str, client_id: str,
    product: str, status: str = "active",
) -> Policy:
    safe_db = wrap(db)
    await _ensure_indexes(safe_db)
    tenant_id = get_tenant_id()
    try:
        await safe_db.policies.insert_one({
            "policy_id": policy_id, "tenant_id": tenant_id,
            "client_id": client_id, "product": product, "status": status,
            "created_at": datetime.now(timezone.utc),
        })
    except DuplicateKeyError:
        pass
    return Policy(
        policy_id=policy_id, tenant_id=tenant_id,
        client_id=client_id, product=product, status=status,
    )


async def find_policy(db, policy_id: str) -> Optional[Policy]:
    safe_db = wrap(db)
    await _ensure_indexes(safe_db)
    r = await safe_db.policies.find_one({"policy_id": policy_id})
    if r is None:
        return None
    return Policy(
        policy_id=r["policy_id"], tenant_id=r["tenant_id"],
        client_id=r["client_id"], product=r["product"],
        status=r["status"],
    )


async def list_policies_for_client(db, client_id: str) -> list[Policy]:
    safe_db = wrap(db)
    await _ensure_indexes(safe_db)
    get_tenant_id()
    out = []
    async for r in safe_db.policies.find({"client_id": client_id}).sort("product", ASCENDING):
        out.append(Policy(
            policy_id=r["policy_id"], tenant_id=r["tenant_id"],
            client_id=r["client_id"], product=r["product"],
            status=r["status"],
        ))
    return out
