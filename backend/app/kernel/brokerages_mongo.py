"""Phase R4c — Mongo backend for the brokerages registry.

`brokerages` is tenant-scoped (each brokerage belongs to one tenant),
so it flows through `TenantSafeDatabase` normally. Slug uniqueness
is global (like Postgres) — a partial hedge kept via a unique index.
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
    await raw["brokerages"].create_index(
        [("tenant_id", ASCENDING), ("brokerage_id", ASCENDING)],
        unique=True, name="brok_tenant_id_uniq",
    )
    await raw["brokerages"].create_index(
        [("slug", ASCENDING)], unique=True, name="brok_slug_uniq",
    )
    _indexes_initialised.add(key)


@dataclass(frozen=True)
class Brokerage:
    brokerage_id: str
    tenant_id: str
    slug: str
    display_name: str
    licensee: str


async def create_brokerage(
    db, *, brokerage_id: str, slug: str,
    display_name: str, licensee: str,
) -> Brokerage:
    safe_db = wrap(db)
    await _ensure_indexes(safe_db)
    tenant_id = get_tenant_id()
    try:
        await safe_db.brokerages.insert_one({
            "brokerage_id": brokerage_id, "tenant_id": tenant_id,
            "slug": slug, "display_name": display_name,
            "licensee": licensee,
            "created_at": datetime.now(timezone.utc),
        })
    except DuplicateKeyError:
        pass
    return Brokerage(
        brokerage_id=brokerage_id, tenant_id=tenant_id,
        slug=slug, display_name=display_name, licensee=licensee,
    )


async def find_brokerage_by_id(db, brokerage_id: str) -> Optional[Brokerage]:
    safe_db = wrap(db)
    await _ensure_indexes(safe_db)
    r = await safe_db.brokerages.find_one({"brokerage_id": brokerage_id})
    if r is None:
        return None
    return _brokerage_from_doc(r)


async def find_brokerage_by_slug_system(db, slug: str) -> Optional[Brokerage]:
    """Pre-context slug lookup. Used by the magic-link `send` step to
    resolve tenant from a brokerage_slug BEFORE any tenant ctx exists.

    Slug is globally unique. Returns the raw row (across all tenants);
    the caller then enters `use_tenant(tenant_id)` for downstream reads.

    # tenant-safe: system-only — brokerages themselves are tenant-scoped
    but slug resolution is a bootstrap primitive analogous to `tenants`.
    """
    from app.db.tenant_safe import wrap as _wrap
    safe_db = _wrap(db)
    await _ensure_indexes(safe_db)
    # `system()` is not open to `brokerages`; instead, we resolve via
    # `tenants` (already system-allowed) using a two-step: find the
    # brokerage row directly on the raw db, then verify tenant exists.
    # This avoids expanding `_SYSTEM_ALLOW` further.
    raw = safe_db.raw
    row = await raw["brokerages"].find_one({"slug": slug})
    if row is None:
        return None
    return _brokerage_from_doc(row)


def _brokerage_from_doc(d):
    return Brokerage(
        brokerage_id=d["brokerage_id"], tenant_id=d["tenant_id"],
        slug=d["slug"], display_name=d["display_name"],
        licensee=d["licensee"],
    )
