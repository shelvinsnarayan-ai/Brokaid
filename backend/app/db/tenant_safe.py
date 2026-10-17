"""brokAId Phase R3 — tenant-safe Mongo access wrapper.

Every read AND write against tenant-scoped Mongo collections MUST
go through `TenantSafeDatabase`. The wrapper auto-injects
`tenant_id = get_tenant_id()` into every filter, document, and
aggregation pipeline; a caller-supplied explicit `tenant_id`
that contradicts the context raises `TenantMismatchError`.

R3 guarantees enforced here (not in the schema)
-----------------------------------------------
1. No cross-tenant reads. `find`, `find_one`, `count_documents`,
   `distinct`, `aggregate` all carry a `tenant_id = ctx` filter.
2. No cross-tenant writes. `insert_one`, `update_*`, `replace_one`,
   `delete_*`, `find_one_and_*`, `bulk_write` all carry a
   `tenant_id = ctx` filter / document.
3. Missing context = fail-loud. `NoTenantContextError` fires on
   the FIRST wrapped call, not on response serialisation.
4. Append-only enforcement. Collections in `APPEND_ONLY_COLLECTIONS`
   (currently `{"events"}`) expose ONLY `insert_one` + reads +
   `create_index`; every non-append method raises
   `AppendOnlyError` structurally (AttributeError-shaped).

System-only escape hatch
------------------------
`TenantSafeDatabase.system(name)` returns the raw
`AsyncIOMotorCollection`. Reserved for tenant registry ops that
happen BEFORE a tenant context exists (index bootstrap, slug
resolution). Callers using `.system(...)` MUST be in a file
carrying a `# tenant-safe: system-only` marker; the
`ci/mongo_raw_collection_scan.js` scanner enforces this.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from motor.motor_asyncio import (
    AsyncIOMotorClient, AsyncIOMotorCollection, AsyncIOMotorDatabase,
)

from app.kernel.tenant_ctx import (
    NoTenantContextError, TenantMismatchError, get_tenant_id,
)


# Collections whose docs are strictly append-only. `events` is the
# ledger. Every other collection may be updated with `update_*` etc.
APPEND_ONLY_COLLECTIONS: frozenset[str] = frozenset({"events"})

# Collections that are system-level (no tenant_id column). Accessed
# via `TenantSafeDatabase.__getitem__` returns the raw collection.
SYSTEM_COLLECTIONS: frozenset[str] = frozenset({"_locks", "_counters"})


class AppendOnlyError(RuntimeError):
    """Attempt to call a non-append write against an append-only collection."""


class SystemCollectionNotAllowed(RuntimeError):
    """Attempt to use `TenantSafeDatabase.system(name)` on a non-allow-listed
    collection. The escape hatch is deliberately narrow: only the tenant
    registry + advisory-lock/counter primitives are reachable through it.
    Any expansion is an explicit code change to `_SYSTEM_ALLOW`."""


# Collections reachable via the `.system()` escape hatch. Kept narrow
# so future code can't bypass tenant filtering on data collections.
_SYSTEM_ALLOW: frozenset[str] = frozenset({
    "tenants",              # tenant registry — pre-context lookups only
    "sessions",             # R4a: session_id → tenant_id discovery
    "verifier_tokens",      # R4c: public cert-verifier — token_hash → tenant_id
    "magic_link_tokens",    # R4c: magic-link validate — token_hash → tenant_id
    "_counters",            # advisory counters
    "_locks",               # advisory locks
    "auth_tokens",          # R1.2a: invite/reset token_hash → tenant_id (pre-auth)
    "auth_lockouts",        # R1.2a: failed-login counters by account/IP (pre-auth)
    "auth_identities",      # R1.2b: one sign-in record per person (email) across tenants
})


TENANT_FIELD = "tenant_id"


def _validate_and_inject(
    filter_or_doc: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return a fresh dict with `tenant_id = ctx`; raise on mismatch."""
    ctx = get_tenant_id()
    out = dict(filter_or_doc or {})
    if TENANT_FIELD in out and out[TENANT_FIELD] != ctx:
        raise TenantMismatchError(
            f"tenant filter/doc {out[TENANT_FIELD]!r} contradicts context {ctx!r}"
        )
    out[TENANT_FIELD] = ctx
    return out


def _validate_and_prefix_pipeline(pipeline: Iterable[Mapping[str, Any]]) -> list:
    """Return `[{$match: {tenant_id: ctx}}, *pipeline]`; raise on mismatch."""
    ctx = get_tenant_id()
    pipe = list(pipeline or [])
    # Fail-loud on any stage that would filter tenant_id incompatibly.
    for i, stage in enumerate(pipe):
        if not isinstance(stage, Mapping):
            continue
        m = stage.get("$match")
        if isinstance(m, Mapping) and TENANT_FIELD in m:
            v = m[TENANT_FIELD]
            if v != ctx:
                raise TenantMismatchError(
                    f"$match stage[{i}] tenant_id={v!r} contradicts context {ctx!r}"
                )
    return [{"$match": {TENANT_FIELD: ctx}}, *pipe]


# ---- Method sets ---------------------------------------------------------
_APPEND_ALLOWED = frozenset({
    "insert_one",
    "find", "find_one", "count_documents", "distinct", "aggregate",
    "create_index", "create_indexes", "drop_indexes", "list_indexes",
    "estimated_document_count",
})


class TenantSafeCollection:
    """A wrapper around one `AsyncIOMotorCollection` that auto-scopes
    every operation to the current tenant.

    Two modes:
      * `mode="append_only"` — only append + read methods available.
        Any other write raises `AppendOnlyError`.
      * `mode="normal"` — full API, with tenant_id auto-inject on
        both filter and document sides.
    """

    __slots__ = ("_coll", "_mode", "_name")

    def __init__(self, coll: AsyncIOMotorCollection, *, mode: str = "normal") -> None:
        if mode not in ("normal", "append_only"):
            raise ValueError(f"mode must be 'normal' or 'append_only', got {mode!r}")
        self._coll = coll
        self._mode = mode
        self._name = coll.name

    # -- reads -----------------------------------------------------------
    async def find_one(self, filter: Optional[Mapping] = None, *args, **kwargs):
        return await self._coll.find_one(_validate_and_inject(filter), *args, **kwargs)

    def find(self, filter: Optional[Mapping] = None, *args, **kwargs):
        return self._coll.find(_validate_and_inject(filter), *args, **kwargs)

    async def count_documents(self, filter: Optional[Mapping] = None, **kwargs):
        return await self._coll.count_documents(_validate_and_inject(filter), **kwargs)

    async def estimated_document_count(self, **kwargs):
        # Motor's method doesn't accept a filter; scope via count_documents.
        return await self.count_documents({}, **kwargs)

    async def distinct(self, key, filter: Optional[Mapping] = None, **kwargs):
        return await self._coll.distinct(key, _validate_and_inject(filter), **kwargs)

    def aggregate(self, pipeline: Iterable[Mapping], *args, **kwargs):
        return self._coll.aggregate(_validate_and_prefix_pipeline(pipeline), *args, **kwargs)

    # -- writes ----------------------------------------------------------
    async def insert_one(self, document: Mapping, **kwargs):
        return await self._coll.insert_one(_validate_and_inject(document), **kwargs)

    async def insert_many(self, documents: Iterable[Mapping], **kwargs):
        if self._mode == "append_only":
            # Append-only allows only `insert_one`. Bulk inserts still
            # write, but the R2 append-only guarantee is stricter: one
            # doc per call so the choke-point can compute a hash.
            raise AppendOnlyError(
                f"insert_many forbidden on append-only collection {self._name!r}; "
                "use insert_one per event."
            )
        scoped = [_validate_and_inject(d) for d in documents]
        return await self._coll.insert_many(scoped, **kwargs)

    async def update_one(self, filter: Mapping, update: Mapping, **kwargs):
        self._require_normal_mode("update_one")
        return await self._coll.update_one(
            _validate_and_inject(filter), update, **kwargs,
        )

    async def update_many(self, filter: Mapping, update: Mapping, **kwargs):
        self._require_normal_mode("update_many")
        return await self._coll.update_many(
            _validate_and_inject(filter), update, **kwargs,
        )

    async def replace_one(self, filter: Mapping, replacement: Mapping, **kwargs):
        self._require_normal_mode("replace_one")
        return await self._coll.replace_one(
            _validate_and_inject(filter), _validate_and_inject(replacement), **kwargs,
        )

    async def delete_one(self, filter: Mapping, **kwargs):
        self._require_normal_mode("delete_one")
        return await self._coll.delete_one(_validate_and_inject(filter), **kwargs)

    async def delete_many(self, filter: Mapping, **kwargs):
        self._require_normal_mode("delete_many")
        return await self._coll.delete_many(_validate_and_inject(filter), **kwargs)

    async def find_one_and_update(self, filter: Mapping, update: Mapping, **kwargs):
        self._require_normal_mode("find_one_and_update")
        return await self._coll.find_one_and_update(
            _validate_and_inject(filter), update, **kwargs,
        )

    async def find_one_and_replace(self, filter: Mapping, replacement: Mapping, **kwargs):
        self._require_normal_mode("find_one_and_replace")
        return await self._coll.find_one_and_replace(
            _validate_and_inject(filter), _validate_and_inject(replacement), **kwargs,
        )

    async def find_one_and_delete(self, filter: Mapping, **kwargs):
        self._require_normal_mode("find_one_and_delete")
        return await self._coll.find_one_and_delete(
            _validate_and_inject(filter), **kwargs,
        )

    async def bulk_write(self, requests, **kwargs):
        raise AppendOnlyError(
            "bulk_write forbidden on tenant-safe collections. "
            "Loop insert_one / update_one instead — the tenant filter "
            "is easier to audit that way. (R2 tightening carried into R3.)"
        )

    # -- index / admin (structural, tenant-agnostic) --------------------
    async def create_index(self, keys, **kwargs):
        return await self._coll.create_index(keys, **kwargs)

    async def create_indexes(self, indexes, **kwargs):
        return await self._coll.create_indexes(indexes, **kwargs)

    async def drop_indexes(self, **kwargs):
        return await self._coll.drop_indexes(**kwargs)

    def list_indexes(self, **kwargs):
        return self._coll.list_indexes(**kwargs)

    @property
    def name(self) -> str:
        return self._name

    # -- helpers --------------------------------------------------------
    def _require_normal_mode(self, method: str) -> None:
        if self._mode == "append_only":
            raise AppendOnlyError(
                f"{method} forbidden on append-only collection {self._name!r}. "
                "The ledger `events` collection accepts inserts only."
            )


class TenantSafeDatabase:
    """Wraps an `AsyncIOMotorDatabase`. `.foo` / `["foo"]` returns a
    `TenantSafeCollection`. `.system(name)` returns the raw
    collection for admin/system ops (scanner-restricted).
    """

    __slots__ = ("_db",)

    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._db = db

    def __getitem__(self, name: str) -> TenantSafeCollection:
        if name in SYSTEM_COLLECTIONS:
            # System collections have no tenant_id column; the wrapper
            # would inject nonsense. Return raw.
            return self._db[name]  # type: ignore[return-value]
        mode = "append_only" if name in APPEND_ONLY_COLLECTIONS else "normal"
        return TenantSafeCollection(self._db[name], mode=mode)

    def __getattr__(self, name: str) -> TenantSafeCollection:
        # `safe_db.events` shorthand.
        if name.startswith("_"):
            raise AttributeError(name)
        return self.__getitem__(name)

    def system(self, name: str) -> AsyncIOMotorCollection:
        """Return the raw collection. Scanner-restricted escape hatch.

        Use ONLY for tenant-registry lookups that happen before a
        tenant context exists (e.g. resolving a slug in
        `trail_mongo.read_events`). Callers MUST be in a file
        carrying the `# tenant-safe: system-only` marker.

        Allow-list is hardcoded (`_SYSTEM_ALLOW`) — any other name
        raises `SystemCollectionNotAllowed`. Expanding the list is
        an explicit code change and shows up in review.
        """
        if name not in _SYSTEM_ALLOW:
            raise SystemCollectionNotAllowed(
                f"collection {name!r} is not on the tenant-safe system "
                f"allow-list ({sorted(_SYSTEM_ALLOW)}). Every data "
                "collection must be accessed through the wrapper."
            )
        return self._db[name]

    @property
    def raw(self) -> AsyncIOMotorDatabase:
        """The underlying Motor database. Scanner-restricted.

        Reserved for `db.command()` calls (ping, server_info) that
        don't touch collections at all. Callers must carry the
        `# tenant-safe: system-only` marker.
        """
        return self._db

    @property
    def client(self) -> AsyncIOMotorClient:
        return self._db.client

    @property
    def name(self) -> str:
        return self._db.name


def wrap(db: AsyncIOMotorDatabase | TenantSafeDatabase) -> TenantSafeDatabase:
    """Idempotent wrapper — returns `db` unchanged if it's already safe."""
    if isinstance(db, TenantSafeDatabase):
        return db
    return TenantSafeDatabase(db)


__all__ = [
    "APPEND_ONLY_COLLECTIONS",
    "AppendOnlyError",
    "NoTenantContextError",
    "SYSTEM_COLLECTIONS",
    "SystemCollectionNotAllowed",
    "TENANT_FIELD",
    "TenantMismatchError",
    "TenantSafeCollection",
    "TenantSafeDatabase",
    "wrap",
]
