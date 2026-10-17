"""Phase 16 — deterministic CI seed pair.

Two named tenants with fixed UUIDs, a frozen clock, byte-identical
genesis + last_hash across every run. Fully isolated from the
Harbourline / Harbour Marine demo seeds (`seed_all`).

Content
-------
For each tenant we materialise:
  * TenantGenesis (baked by create_tenant, using the frozen clock)
  * base ClientEntityCreated + PolicyCreated
  * one full endorsement cycle: ClientChangeRequested → SubmissionDrafted
        → CarrierQuoted → EndorsementQuoteReady → Approval
        → PaymentMandateCreated → PaymentInitiated → PaymentReceived
        → SignatureRequested → SignatureCompleted → EndorsementExecuted
        → CertificateRegenerated → VerifierTokenIssued
  * one full claim cycle:      ClaimFNOL → ClaimLodged → ClaimAcknowledged
        → ClaimStatusUpdate → Notification

Determinism controls
--------------------
  * Fixed tenant UUIDs (see constants below).
  * Frozen clock anchored at `ANCHOR_ISO` (2024-01-01T00:00:00Z), each
    event's ts is anchor + N seconds where N is the deterministic
    per-tenant offset (see `_ts()`).
  * All IDs derived via uuid5 with a stable namespace and per-item name
    so no `uuid4()` randomness leaks into hashes.
  * `mocked=True` on every external-channel event.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import uuid
from datetime import datetime, timedelta
from typing import Any, Iterator


from app.kernel import trail as trail_module  # noqa: F401  (kept for future clock injection)
from app.kernel.canonicalize import event_hash as _event_hash, jcs
from app.kernel.trail import GENESIS_PREV, Event  # noqa: F401


# ---- Shared event sequence (Postgres + Mongo) ----------------------------
def _build_event_specs(slug: str, tenant_offset: int) -> list[dict[str, Any]]:
    """Return the deterministic event sequence for a CI tenant.

    Shared between the Postgres and Mongo seeders so byte-identical
    hashes are guaranteed by construction. Any change here changes
    both stores in lock-step; that is the whole point of R2.

    `tenant_offset` is the same day-count both seeders pass — it
    affects the certificate payload's `issued_at` (tenant B lands
    30 days after tenant A).
    """
    client_id = _id(f"{slug}:client:acme")
    policy_id = _id(f"{slug}:policy:acme-liab")
    envelope_id = _id(f"{slug}:env:endorse-1")
    quote_id = _id(f"{slug}:quote:1")
    claim_id = _id(f"{slug}:claim:1")
    cert_id = _id(f"{slug}:cert:1")
    # Postgres seed used `base.isoformat()` (default timespec) — keep
    # exact string for hash parity, including the trailing-zero-µs strip.
    base_iso = (ANCHOR + timedelta(days=tenant_offset)).isoformat()

    return [
        # Base
        {"type": "ClientEntityCreated",
         "payload": {"client_id": client_id, "display_name": "Acme Widgets"},
         "actor": {"kind": "system", "id": "seed", "name": "seed"}},
        {"type": "PolicyCreated",
         "payload": {"policy_id": policy_id, "client_id": client_id,
                     "product": "General Liability"},
         "actor": {"kind": "system", "id": "seed", "name": "seed"}},
        # Endorsement
        {"type": "ClientChangeRequested",
         "payload": {"envelope_id": envelope_id, "policy_id": policy_id,
                     "client_id": client_id,
                     "raw_message": "increase liability limit"},
         "actor": {"kind": "human", "id": "ci-owner", "name": "owner"}},
        {"type": "SubmissionDrafted",
         "payload": {"envelope_id": envelope_id, "policy_id": policy_id,
                     "submission_summary": "raise limit to $10M"},
         "actor": {"kind": "system", "id": "placement", "name": "placement"}},
        {"type": "CarrierQuoted",
         "payload": {"envelope_id": envelope_id, "policy_id": policy_id,
                     "quote_id": quote_id,
                     "premium_change_amount": "1200",
                     "premium_currency": "AUD"},
         "actor": {"kind": "system", "id": "carrier", "name": "carrier"},
         "mocked": True},
        {"type": "EndorsementQuoteReady",
         "payload": {"envelope_id": envelope_id, "policy_id": policy_id,
                     "quote_id": quote_id},
         "actor": {"kind": "system", "id": "orchestrator", "name": "orch"}},
        {"type": "Approval",
         "payload": {"envelope_id": envelope_id, "class_key": "endorsement_standard",
                     "decision": "approve"},
         "actor": {"kind": "human", "id": "ci-principal", "name": "principal"}},
        {"type": "PaymentMandateCreated",
         "payload": {"envelope_id": envelope_id, "policy_id": policy_id,
                     "mandate_id": _id(f"{slug}:mandate:1"),
                     "amount": "1200", "currency": "AUD"},
         "actor": {"kind": "human", "id": "ci-owner", "name": "owner"},
         "mocked": True},
        {"type": "PaymentInitiated",
         "payload": {"envelope_id": envelope_id, "policy_id": policy_id,
                     "amount": "1200", "currency": "AUD",
                     "rail_label": "PayTo"},
         "actor": {"kind": "system", "id": "payment", "name": "payment"},
         "mocked": True},
        {"type": "PaymentReceived",
         "payload": {"envelope_id": envelope_id, "policy_id": policy_id,
                     "amount": "1200", "currency": "AUD"},
         "actor": {"kind": "system", "id": "payment", "name": "payment"},
         "mocked": True},
        {"type": "SignatureRequested",
         "payload": {"envelope_id": envelope_id, "policy_id": policy_id,
                     "request_id": _id(f"{slug}:esign-req:1")},
         "actor": {"kind": "system", "id": "e_sign", "name": "e_sign"},
         "mocked": True},
        {"type": "SignatureCompleted",
         "payload": {"envelope_id": envelope_id, "policy_id": policy_id,
                     "signature_id": _id(f"{slug}:esign-sig:1")},
         "actor": {"kind": "human", "id": "ci-owner", "name": "owner"},
         "mocked": True},
        {"type": "EndorsementExecuted",
         "payload": {"envelope_id": envelope_id, "policy_id": policy_id},
         "actor": {"kind": "system", "id": "endorsement", "name": "endorsement"}},
        {"type": "CertificateRegenerated",
         "payload": {"envelope_id": envelope_id, "policy_id": policy_id,
                     "certificate_id": cert_id,
                     "certificate": {"issued_at": base_iso,
                                      "insurer_display": "Fictional Re",
                                      "product": "General Liability"},
                     "mocked": True},
         "actor": {"kind": "system", "id": "certificate", "name": "certificate"},
         "mocked": True},
        {"type": "VerifierTokenIssued",
         "payload": {"envelope_id": envelope_id, "certificate_id": cert_id,
                     "policy_id": policy_id,
                     "token_hash": hashlib.sha256(f"{slug}:token:1".encode()).hexdigest()},
         "actor": {"kind": "system", "id": "verifier", "name": "verifier"}},
        # Claim cycle
        {"type": "ClaimFNOL",
         "payload": {"claim_id": claim_id, "policy_id": policy_id,
                     "client_id": client_id,
                     "loss_summary": "warehouse water damage"},
         "actor": {"kind": "human", "id": "ci-owner", "name": "owner"}},
        {"type": "ClaimLodged",
         "payload": {"claim_id": claim_id, "policy_id": policy_id,
                     "insurer_ref": f"{slug}-CLM-0001"},
         "actor": {"kind": "system", "id": "insurer_channel", "name": "insurer"},
         "mocked": True},
        {"type": "ClaimAcknowledged",
         "payload": {"claim_id": claim_id,
                     "insurer_ref": f"{slug}-CLM-0001"},
         "actor": {"kind": "system", "id": "insurer_channel", "name": "insurer"},
         "mocked": True},
        {"type": "ClaimStatusUpdate",
         "payload": {"claim_id": claim_id, "status": "under_review"},
         "actor": {"kind": "human", "id": "ci-principal", "name": "principal"}},
        {"type": "Notification",
         "payload": {"claim_id": claim_id, "channel": "email",
                     "template": "claim_status_update"},
         "actor": {"kind": "system", "id": "notify", "name": "notify"},
         "mocked": True},
    ]


# ---- Anchor + namespaces --------------------------------------------------
ANCHOR_ISO = "2024-01-01T00:00:00.000000+00:00"
ANCHOR = datetime.fromisoformat(ANCHOR_ISO)
UUID_NS = uuid.UUID("00000000-0000-4000-8000-000000000000")


def _id(name: str) -> str:
    return str(uuid.uuid5(UUID_NS, name))


# ---- Fixed tenants --------------------------------------------------------
CI_TENANT_A_ID = _id("ci:tenant-a")
CI_TENANT_A_SLUG = "ci-alpha"
CI_TENANT_A_NAME = "CI Alpha (fictional)"

CI_TENANT_B_ID = _id("ci:tenant-b")
CI_TENANT_B_SLUG = "ci-beta"
CI_TENANT_B_NAME = "CI Beta (fictional)"


# ---- Frozen clock ---------------------------------------------------------
class _FrozenClock:
    def __init__(self, start: datetime):
        self._t = start
        self._step_s = 1

    def now(self, tz: Any = None) -> datetime:
        v = self._t
        self._t = self._t + timedelta(seconds=self._step_s)
        if tz is None:
            return v
        return v.astimezone(tz)


@contextlib.contextmanager
def frozen_clock(anchor: datetime = ANCHOR) -> Iterator[_FrozenClock]:
    """Yield an offset-aware UTC datetime for a monotonic frozen clock,
    stepping 1 second per call. Kept in-module — external code should
    just use the seeder directly, which pins each event's ts explicitly.
    """
    clock = _FrozenClock(anchor)
    yield clock


# ---- Low-level seed helpers -----------------------------------------------
async def _upsert_tenant(
    pool: object, tenant_id: str, slug: str, display_name: str,
) -> None:
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO tenants (tenant_id, slug, display_name, "
            "                     genesis_hash, created_at) "
            "VALUES ($1::uuid, $2, $3, $4, $5) "
            "ON CONFLICT (tenant_id) DO NOTHING",
            tenant_id, slug, display_name, GENESIS_PREV, ANCHOR,
        )


async def _seed_events(
    pool: object, tenant_id: str, slug: str, tenant_offset: int,
) -> None:
    """Append a fixed sequence of events at deterministic timestamps."""
    # Skip when the full sequence is already present. We check for a
    # sentinel type ("Notification") that only lands at the end.
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id,
            )
            done = await c.fetchval(
                "SELECT count(*) FROM events "
                "WHERE tenant_id=$1::uuid AND type='Notification'",
                tenant_id,
            )
    if done:
        return

    base = ANCHOR + timedelta(days=tenant_offset)
    tick = [0]

    def ts() -> datetime:
        tick[0] += 1
        return base + timedelta(seconds=tick[0])

    # Sequence (in append order): shared with the Mongo seeder — see
    # `_build_event_specs()` at the top of this module.
    sequence: list[dict[str, Any]] = _build_event_specs(slug, tenant_offset)

    # Insert via a hand-written append loop that pins each event's ts.
    # This bypasses trail.append's ambient clock so we get bit-identical
    # hashes run-to-run.
    prev = GENESIS_PREV
    # TenantGenesis is written by _upsert_tenant path — we must chain
    # after it. Re-read the current tip.
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id,
            )
            row = await c.fetchrow(
                "SELECT event_hash FROM events "
                "WHERE tenant_id=$1::uuid ORDER BY seq DESC LIMIT 1",
                tenant_id,
            )
            if row:
                prev = row["event_hash"]

            for i, spec in enumerate(sequence):
                ev_ts = ts()
                event_id = _id(f"{slug}:ev:{i:03d}")
                payload = spec["payload"]
                actor = spec["actor"]
                idem = f"ci:{slug}:{spec['type']}:{i:03d}"
                mocked = bool(spec.get("mocked", False))
                pjcs = jcs(payload)
                ejh = _event_hash(
                    event_id=event_id,
                    ts_iso=ev_ts.isoformat(timespec="microseconds"),
                    tenant_id=tenant_id,
                    actor=actor,
                    type=spec["type"],
                    payload_jcs=pjcs,
                    prev_hash=prev,
                )
                await c.execute(
                    "INSERT INTO events "
                    "  (event_id, tenant_id, ts, actor, type, payload, "
                    "   payload_jcs, prev_hash, event_hash, "
                    "   idempotency_key, mocked, created_at) "
                    "VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, "
                    "        $7, $8, $9, $10, $11, $3) "
                    "ON CONFLICT (tenant_id, idempotency_key) DO NOTHING",
                    event_id, tenant_id, ev_ts,
                    json.dumps(actor), spec["type"], json.dumps(payload),
                    pjcs, prev, ejh, idem, mocked,
                )
                prev = ejh


async def _seed_tenant(
    pool: object, tenant_id: str, slug: str, name: str,
    offset_days: int,
) -> None:
    # Genesis: create_tenant is not idempotent, so we do it inline.
    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT genesis_hash FROM tenants WHERE tenant_id=$1::uuid",
            tenant_id,
        )
    if row is None:
        await _upsert_tenant(pool, tenant_id, slug, name)
        # Write TenantGenesis at a deterministic ts, then bake genesis.
        gen_ts = ANCHOR
        event_id = _id(f"{slug}:genesis")
        payload = {"tenant_slug": slug, "display_name": name}
        actor = {"kind": "system", "id": "seed", "name": "seed"}
        pjcs = jcs(payload)
        ejh = _event_hash(
            event_id=event_id,
            ts_iso=gen_ts.isoformat(timespec="microseconds"),
            tenant_id=tenant_id,
            actor=actor,
            type="TenantGenesis",
            payload_jcs=pjcs,
            prev_hash=GENESIS_PREV,
        )
        async with pool.acquire() as c:
            async with c.transaction():
                await c.execute(
                    "SELECT set_config('app.tenant_id', $1, true)", tenant_id,
                )
                await c.execute(
                    "INSERT INTO events "
                    "  (event_id, tenant_id, ts, actor, type, payload, "
                    "   payload_jcs, prev_hash, event_hash, "
                    "   idempotency_key, mocked, created_at) "
                    "VALUES ($1::uuid, $2::uuid, $3, $4, 'TenantGenesis', "
                    "        $5, $6, $7, $8, $9, false, $3) "
                    "ON CONFLICT (tenant_id, idempotency_key) DO NOTHING",
                    event_id, tenant_id, gen_ts,
                    json.dumps(actor), json.dumps(payload),
                    pjcs, GENESIS_PREV, ejh, f"ci:{slug}:genesis",
                )
                await c.execute(
                    "UPDATE tenants SET genesis_hash=$1 "
                    "WHERE tenant_id=$2::uuid AND genesis_hash=$3",
                    ejh, tenant_id, GENESIS_PREV,
                )

    await _seed_events(pool, tenant_id, slug, offset_days)


async def seed_ci_deterministic(pool: object) -> dict[str, Any]:
    """Idempotent. Safe to run twice; the second run is a no-op."""
    await _seed_tenant(
        pool, CI_TENANT_A_ID, CI_TENANT_A_SLUG, CI_TENANT_A_NAME,
        offset_days=0,
    )
    await _seed_tenant(
        pool, CI_TENANT_B_ID, CI_TENANT_B_SLUG, CI_TENANT_B_NAME,
        offset_days=30,
    )
    async with pool.acquire() as c:
        rows = await c.fetch(
            "SELECT slug, tenant_id::text, genesis_hash FROM tenants "
            "WHERE tenant_id = ANY($1::uuid[]) ORDER BY slug",
            [CI_TENANT_A_ID, CI_TENANT_B_ID],
        )
    return {
        "tenants": [dict(r) for r in rows],
        "tenant_a_id": CI_TENANT_A_ID,
        "tenant_b_id": CI_TENANT_B_ID,
    }


# ---- Mongo seeder (Phase R2 dual-ledger parity) --------------------------
async def _seed_events_mongo(
    db, tenant_id: str, slug: str, tenant_offset: int,
) -> None:
    """Append the same fixed sequence into Mongo, byte-identical to Postgres.

    Uses `trail_mongo.append_event()` — the choke-point — with pinned
    `ts_override` and `event_id_override` so hashes match the Postgres
    seeder exactly. `DuplicateIdempotencyKey` on retry is expected and
    silently swallowed (idempotence).

    R3: writes go through the tenant-safe wrapper. Caller must have
    entered `use_tenant(tenant_id)` — enforced by `_seed_tenant_mongo`.
    """
    from app.kernel import trail_mongo
    from app.kernel.tenant_ctx import get_tenant_id

    # Sanity: context is set to the target tenant.
    ctx = get_tenant_id()
    if ctx != tenant_id:
        raise RuntimeError(
            f"seed context mismatch: ctx={ctx!r} tenant_id={tenant_id!r}"
        )

    # Idempotence: bail if the Notification tail sentinel is already present.
    from app.db.tenant_safe import wrap as _wrap
    safe_db = _wrap(db)
    done = await safe_db.events.find_one({"type": "Notification"})
    if done is not None:
        return

    base = ANCHOR + timedelta(days=tenant_offset)
    sequence = _build_event_specs(slug, tenant_offset)
    for i, spec in enumerate(sequence):
        ev_ts = base + timedelta(seconds=i + 1)
        event_id = _id(f"{slug}:ev:{i:03d}")
        idem = f"ci:{slug}:{spec['type']}:{i:03d}"
        try:
            await trail_mongo.append_event(
                safe_db, tenant_id, spec["type"],
                actor=spec["actor"], payload=spec["payload"],
                idempotency_key=idem,
                mocked=bool(spec.get("mocked", False)),
                ts_override=ev_ts,
                event_id_override=event_id,
            )
        except trail_mongo.DuplicateIdempotencyKey:
            # Idempotent retry — already seeded.
            pass


async def _seed_tenant_mongo(
    db, tenant_id: str, slug: str, name: str, offset_days: int,
) -> None:
    """Mirror of `_seed_tenant()` against the Mongo store.

    Enters `use_tenant(tenant_id)` before writing so every touch of
    the tenant-safe wrapper carries the right context. TenantGenesis
    goes through `trail_mongo.create_tenant()` (the choke-point).
    """
    from app.kernel import trail_mongo
    from app.kernel.tenant_ctx import use_tenant
    from app.db.tenant_safe import wrap as _wrap

    safe_db = _wrap(db)
    async with use_tenant(tenant_id):
        # tenant-safe: system-only — pre-context lookup to check whether
        # this tenant already exists. We're already inside use_tenant,
        # so a wrapper find_one would auto-inject the same tenant_id
        # anyway; system() keeps the intent explicit.
        existing = await safe_db.system("tenants").find_one({"tenant_id": tenant_id})
        if existing is None:
            gen_ts = ANCHOR
            gen_event_id = _id(f"{slug}:genesis")
            await trail_mongo.create_tenant(
                safe_db, slug=slug, display_name=name,
                tenant_id_override=tenant_id,
                ts_override=gen_ts,
                event_id_override=gen_event_id,
                idempotency_key_override=f"ci:{slug}:genesis",
                actor_override={"kind": "system", "id": "seed", "name": "seed"},
                payload_override={"tenant_slug": slug, "display_name": name},
            )

        await _seed_events_mongo(safe_db, tenant_id, slug, offset_days)


async def seed_ci_deterministic_mongo(db) -> dict[str, Any]:
    """Mongo mirror of `seed_ci_deterministic()`. Byte-identical hashes.

    Idempotent — a second call is a no-op.

    R3: the two `_seed_tenant_mongo` calls each enter their own
    `use_tenant(...)` scope. The final registry summary uses the
    `.system("tenants")` escape hatch (documented above) since it
    spans two tenants intentionally and runs outside any single
    tenant context.
    """
    await _seed_tenant_mongo(
        db, CI_TENANT_A_ID, CI_TENANT_A_SLUG, CI_TENANT_A_NAME,
        offset_days=0,
    )
    await _seed_tenant_mongo(
        db, CI_TENANT_B_ID, CI_TENANT_B_SLUG, CI_TENANT_B_NAME,
        offset_days=30,
    )
    # tenant-safe: system-only — summary spans both tenants for the seed
    # CLI's return value; the escape hatch is the only shape that lets
    # a system-context caller list all tenants deliberately.
    from app.db.tenant_safe import wrap as _wrap
    safe_db = _wrap(db)
    tenants = []
    async for doc in safe_db.system("tenants").find(
        {"tenant_id": {"$in": [CI_TENANT_A_ID, CI_TENANT_B_ID]}}
    ).sort("slug", 1):
        tenants.append({
            "slug": doc["slug"],
            "tenant_id": doc["tenant_id"],
            "genesis_hash": doc["genesis_hash"],
        })
    return {
        "tenants": tenants,
        "tenant_a_id": CI_TENANT_A_ID,
        "tenant_b_id": CI_TENANT_B_ID,
    }