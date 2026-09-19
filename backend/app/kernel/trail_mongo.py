"""brokAId kernel — trAIl (ledger) surface on MongoDB.

Phase R2: dual-ledger with byte-identical hashes to Postgres.
Phase R3: append + read go through `TenantSafeDatabase`. A
missing tenant context blows up on the first collection touch;
a cross-tenant filter blows up before the query hits Mongo.

# tenant-safe: system-only
This module has the marker because:
  1. `_ensure_indexes()` creates indexes BEFORE any tenant
     context exists (index setup is a schema-level op).
  2. `read_events()` resolves a `tenant_ref` (slug OR UUID) via
     `safe_db.system("tenants")` — an unfiltered lookup that
     compares to the ctx and raises `TenantMismatchError` on
     cross-tenant access. Doing the resolution through the
     safe wrapper would produce `LookupError` instead, which
     hides the leak intent (see R3 acceptance criteria).

Every actual event write and every event read STILL flow through
the safe wrapper: `safe_db.events.insert_one(...)` /
`safe_db.events.find(...)`. The system escape is used only for
tenant registry lookups.

Append-only enforcement
-----------------------
The `events` collection is exposed as an append-only
`TenantSafeCollection` (mode="append_only"). Any non-insert
write raises `AppendOnlyError` — structurally, without a runtime
grep. The R2 grep scanner (`ci/mongo_events_write_scan.js`)
stays green as a belt-and-braces sanity net.
"""
from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

from app.db.tenant_safe import TenantSafeDatabase, wrap as _wrap_safe_db
from app.kernel.canonicalize import event_hash, jcs
from app.kernel.tenant_ctx import (
    NoTenantContextError, TenantMismatchError, get_tenant_id, use_tenant,
)

GENESIS_PREV = "0" * 64


_CLIENT_RELEASE_EVENT_TYPES: frozenset[str] = frozenset({
    "ClientMessageReleased",
    "ClientMessageRewriteRequired",
    "ClientMessageHITL",
})


class ClassifierVerdictMissing(ValueError):
    """A ClientMessage* event was written without `classifier_verdict`."""


class DuplicateIdempotencyKey(ValueError):
    """Raised when `append_event()` sees a duplicate (tenant_id, idempotency_key)."""


@dataclass
class Event:
    event_id: str
    tenant_id: str
    seq: int
    ts: str
    actor: dict
    type: str
    payload: dict
    payload_jcs: str
    prev_hash: str
    event_hash: str
    idempotency_key: str
    mocked: bool


@dataclass
class Tenant:
    tenant_id: str
    slug: str
    display_name: str
    genesis_hash: str


# ---- Per-tenant asyncio locks --------------------------------------------
_tenant_locks: dict[str, asyncio.Lock] = {}
_locks_registry_lock = asyncio.Lock()


async def _tenant_lock(tenant_id: str) -> asyncio.Lock:
    async with _locks_registry_lock:
        lk = _tenant_locks.get(tenant_id)
        if lk is None:
            lk = asyncio.Lock()
            _tenant_locks[tenant_id] = lk
        return lk


# ---- Index bootstrap (idempotent, tenant-agnostic) -----------------------
_indexes_initialised: set[int] = set()


async def _ensure_indexes(safe_db: TenantSafeDatabase) -> None:
    # tenant-safe: system-only
    # Indexes are structural — no tenant filter. Use the raw db to avoid
    # tripping the safe-wrapper's tenant-inject on index management.
    raw = safe_db.raw
    key = id(raw)
    if key in _indexes_initialised:
        return
    events = raw["events"]
    tenants = raw["tenants"]
    await events.create_index(
        [("tenant_id", ASCENDING), ("seq", ASCENDING)],
        unique=True, name="tenant_seq_uniq",
    )
    await events.create_index(
        [("tenant_id", ASCENDING), ("idempotency_key", ASCENDING)],
        unique=True, name="tenant_idem_uniq",
    )
    await events.create_index(
        [("event_hash", ASCENDING)],
        unique=True, name="event_hash_uniq",
    )
    await tenants.create_index(
        [("tenant_id", ASCENDING)], unique=True, name="tenant_id_uniq",
    )
    await tenants.create_index(
        [("slug", ASCENDING)], unique=True, name="tenant_slug_uniq",
    )
    _indexes_initialised.add(key)


# ---- Core append -----------------------------------------------------------
async def _last_event_doc(safe_db: TenantSafeDatabase):
    """Fetch the current tip event for the CURRENT tenant, or None."""
    return await safe_db.events.find_one({}, sort=[("seq", -1)])


async def append_event(
    db: AsyncIOMotorDatabase | TenantSafeDatabase,
    tenant_id: str,
    type: str,
    actor: dict,
    payload: dict,
    idempotency_key: str,
    mocked: bool = False,
    *,
    ts_override: datetime | None = None,
    event_id_override: str | None = None,
) -> Event:
    """Append one event to a tenant's chain. Choke-point for Mongo writes.

    Requires an active tenant context (`use_tenant(tenant_id)` or the
    request middleware) whose value matches `tenant_id`. Belt-and-
    braces: `tenant_id` is passed both in the context (via the safe
    wrapper) AND explicitly in the doc.

    Raises
    ------
    NoTenantContextError
        If no tenant is bound to the current context.
    TenantMismatchError
        If the bound context contradicts `tenant_id`.
    ClassifierVerdictMissing
        On any ClientMessage* event without classifier_verdict.
    DuplicateIdempotencyKey
        On a duplicate (tenant_id, idempotency_key).
    """
    if type in _CLIENT_RELEASE_EVENT_TYPES and "classifier_verdict" not in (payload or {}):
        raise ClassifierVerdictMissing(
            f"{type} events require 'classifier_verdict' in payload"
        )

    ctx = get_tenant_id()  # fail-loud on missing context
    if ctx != tenant_id:
        raise TenantMismatchError(
            f"append_event tenant_id={tenant_id!r} contradicts context {ctx!r}"
        )

    safe_db = _wrap_safe_db(db)
    await _ensure_indexes(safe_db)

    lock = await _tenant_lock(tenant_id)
    async with lock:
        tip = await _last_event_doc(safe_db)
        prev = tip["event_hash"] if tip else GENESIS_PREV
        seq = (int(tip["seq"]) + 1) if tip else 1

        event_id = event_id_override or str(uuid.uuid4())
        ts_dt = ts_override or datetime.now(timezone.utc)
        ts_iso = ts_dt.isoformat(timespec="microseconds")
        payload_jcs = jcs(payload)
        eh = event_hash(
            event_id=event_id,
            ts_iso=ts_iso,
            tenant_id=tenant_id,
            actor=actor,
            type=type,
            payload_jcs=payload_jcs,
            prev_hash=prev,
        )
        doc = {
            "event_id": event_id,
            "tenant_id": tenant_id,      # belt-and-braces; wrapper also injects
            "seq": seq,
            "ts": ts_iso,
            "actor": actor,
            "type": type,
            "payload": payload,
            "payload_jcs": payload_jcs,
            "prev_hash": prev,
            "event_hash": eh,
            "idempotency_key": idempotency_key,
            "mocked": bool(mocked),
            "created_at": ts_dt,
        }
        try:
            await safe_db.events.insert_one(doc)
        except DuplicateKeyError as e:
            raise DuplicateIdempotencyKey(
                f"duplicate (tenant_id, idempotency_key)={tenant_id},{idempotency_key}"
            ) from e

        ev_obj = Event(
            event_id=event_id, tenant_id=tenant_id, seq=seq, ts=ts_iso,
            actor=actor, type=type, payload=payload,
            payload_jcs=payload_jcs, prev_hash=prev, event_hash=eh,
            idempotency_key=idempotency_key, mocked=bool(mocked),
        )

        # R5a-1: in-line projection dispatch — mirror trail.py::_project's
        # transaction-local projection call. Keeps the "projection is
        # part of the append" invariant across backends. Silent no-op
        # on unknown event types.
        try:
            from app.kernel import workflow_mongo
            await workflow_mongo.project_event(safe_db, ev_obj)
        except Exception:
            # Projection failure must not corrupt the ledger. Log the
            # miss at some point (R5c: chain-verify surfaces it) but
            # keep the write.
            pass

        return ev_obj


# ---- Tenant creation -------------------------------------------------------
async def create_tenant(
    db: AsyncIOMotorDatabase | TenantSafeDatabase,
    slug: str,
    display_name: str,
    *,
    tenant_id_override: str | None = None,
    ts_override: datetime | None = None,
    event_id_override: str | None = None,
    idempotency_key_override: str | None = None,
    actor_override: dict | None = None,
    payload_override: dict | None = None,
) -> tuple[Tenant, Event]:
    """Create a tenant and its TenantGenesis event.

    Caller is responsible for entering `use_tenant(new_tenant_id)`
    BEFORE calling this — the wrapper's auto-inject scopes both
    the tenants insert and the TenantGenesis event to the new
    tenant. `tenant_id_override` sets the UUID; without it a
    fresh one is generated but you must have set the context to
    it first (which is awkward, hence override for the seed path).
    """
    safe_db = _wrap_safe_db(db)
    await _ensure_indexes(safe_db)
    tenant_id = tenant_id_override or get_tenant_id()

    # Sanity: context must match the tenant we're creating.
    ctx = get_tenant_id()
    if ctx != tenant_id:
        raise TenantMismatchError(
            f"create_tenant tenant_id_override={tenant_id!r} "
            f"contradicts context {ctx!r} — set `use_tenant({tenant_id!r})` first."
        )

    try:
        await safe_db.tenants.insert_one({
            "tenant_id": tenant_id,
            "slug": slug,
            "display_name": display_name,
            "genesis_hash": GENESIS_PREV,
            "created_at": ts_override or datetime.now(timezone.utc),
        })
    except DuplicateKeyError as e:
        raise ValueError(f"tenant already exists: slug={slug}") from e

    actor = actor_override or {"kind": "system", "id": "trail", "name": "trAIl"}
    payload = payload_override if payload_override is not None else {
        "slug": slug, "display_name": display_name,
    }
    ev = await append_event(
        safe_db, tenant_id, "TenantGenesis",
        actor=actor, payload=payload,
        idempotency_key=idempotency_key_override or f"genesis:{slug}",
        ts_override=ts_override,
        event_id_override=event_id_override,
    )

    r = await safe_db.tenants.update_one(
        {"tenant_id": tenant_id, "genesis_hash": GENESIS_PREV},
        {"$set": {"genesis_hash": ev.event_hash}},
    )
    if r.matched_count != 1:
        raise RuntimeError(
            f"genesis_hash freeze failed for tenant={tenant_id} — "
            "tenant vanished or genesis was already frozen"
        )

    return (
        Tenant(
            tenant_id=tenant_id, slug=slug,
            display_name=display_name, genesis_hash=ev.event_hash,
        ),
        ev,
    )


# ---- Read side -------------------------------------------------------------
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


async def _resolve_tenant_ref(
    safe_db: TenantSafeDatabase, tenant_ref: str,
) -> dict:
    """Return the tenants doc for `tenant_ref`, or raise.

    # tenant-safe: system-only — uses `safe_db.system("tenants")`
    to disambiguate `LookupError` (unknown slug) from
    `TenantMismatchError` (slug belongs to another tenant). The
    method compares the resolved tenant_id against the current
    context BEFORE returning the doc; no cross-tenant data ever
    leaves this function.
    """
    ctx = get_tenant_id()

    # UUID path: mismatch is unambiguously cross-tenant → fail-loud
    # BEFORE touching Mongo.
    if _UUID_RE.match(tenant_ref):
        if tenant_ref != ctx:
            raise TenantMismatchError(
                f"read_events tenant_ref={tenant_ref!r} contradicts context {ctx!r}"
            )
        t_doc = await safe_db.system("tenants").find_one({"tenant_id": tenant_ref})
        if t_doc is None:
            raise LookupError(f"unknown tenant: {tenant_ref}")
        return t_doc

    # Slug path: system-lookup by slug; then compare tenant_id to ctx.
    t_doc = await safe_db.system("tenants").find_one({"slug": tenant_ref})
    if t_doc is None:
        raise LookupError(f"unknown tenant: {tenant_ref}")
    if t_doc["tenant_id"] != ctx:
        raise TenantMismatchError(
            f"read_events tenant_ref={tenant_ref!r} resolves to "
            f"tenant_id={t_doc['tenant_id']!r} which contradicts context {ctx!r}"
        )
    return t_doc


async def read_events(
    db: AsyncIOMotorDatabase | TenantSafeDatabase,
    tenant_ref: str,
    from_seq: int | None = None,
    to_seq: int | None = None,
) -> tuple[Tenant, list[Event]]:
    """Load a tenant + its events. `tenant_ref` = slug OR tenant_id UUID.

    Requires an active tenant context matching the resolved
    tenant. Cross-tenant refs raise `TenantMismatchError` before
    any event data is touched.
    """
    safe_db = _wrap_safe_db(db)
    t_doc = await _resolve_tenant_ref(safe_db, tenant_ref)

    tenant = Tenant(
        tenant_id=t_doc["tenant_id"],
        slug=t_doc["slug"],
        display_name=t_doc["display_name"],
        genesis_hash=t_doc["genesis_hash"],
    )

    q: dict[str, Any] = {}
    if from_seq is not None or to_seq is not None:
        rng: dict[str, Any] = {}
        if from_seq is not None:
            rng["$gte"] = from_seq
        if to_seq is not None:
            rng["$lte"] = to_seq
        q["seq"] = rng

    events: list[Event] = []
    cursor = safe_db.events.find(q).sort("seq", ASCENDING)
    async for r in cursor:
        events.append(Event(
            event_id=r["event_id"],
            tenant_id=r["tenant_id"],
            seq=int(r["seq"]),
            ts=r["ts"],
            actor=r["actor"],
            type=r["type"],
            payload=r["payload"],
            payload_jcs=r["payload_jcs"],
            prev_hash=r["prev_hash"],
            event_hash=r["event_hash"],
            idempotency_key=r["idempotency_key"],
            mocked=bool(r["mocked"]),
        ))
    return tenant, events


def event_to_json(ev: Event) -> dict[str, Any]:
    return {
        "event_id": ev.event_id,
        "tenant_id": ev.tenant_id,
        "seq": ev.seq,
        "ts": ev.ts,
        "actor": ev.actor,
        "type": ev.type,
        "payload": ev.payload,
        "payload_jcs": ev.payload_jcs,
        "prev_hash": ev.prev_hash,
        "event_hash": ev.event_hash,
        "idempotency_key": ev.idempotency_key,
        "mocked": ev.mocked,
    }
