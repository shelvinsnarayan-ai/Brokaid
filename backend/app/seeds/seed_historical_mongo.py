"""Phase R5c-2a — Mongo variant of phase-7 historical seed.

Byte-for-byte mirror of `app.seeds.seed_historical` — seeds
`proj_class_unlock` rows for 10 classes, emits ~30 Approval events
across those classes with the deterministic RNG (seed
`SEED_RNG=20261101`), and emits a `SeedMarker` for idempotency.
`seed_queue_cards_mongo` mirrors the 5-card queue projection +
proposal events + `SeedMarker`.

All ledger events flow through `trail_dispatch.append_via_dispatch`
(byte-identical hash chain guarantee across backends). Queue
projection uses the dispatch-aware `workflow.enqueue`.
"""
from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError
from app.kernel.trail_mongo import DuplicateIdempotencyKey

from app.db.tenant_safe import wrap
from app.kernel import workflow
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch
from app.seeds.seed_historical import (
    CARD_SPECS, CARDS_KEY, CLASSES_FOR_HISTORY,
    HISTORICAL_KEY, SEED_RNG, _components, _scalar,
)


def _rng() -> random.Random:
    return random.Random(SEED_RNG)


async def _already_seeded_mongo(db, tenant_id: str, key: str) -> bool:
    safe = wrap(db)
    async with use_tenant(tenant_id):
        row = await safe.events.find_one({"idempotency_key": key})
    return bool(row)


async def seed_historical_decisions_mongo(
    db: AsyncIOMotorDatabase, tenant_id: str, *, count: int = 30,
) -> dict:
    if await _already_seeded_mongo(db, tenant_id, HISTORICAL_KEY):
        return {"seeded": False, "reason": "already_seeded"}

    rng = _rng()
    safe = wrap(db)
    async with use_tenant(tenant_id):
        # Drive proj_class_unlock rows directly (same shape as the PG
        # seeder, which bypasses the ClassUnlocked/Relocked protocol
        # for historical-only data — see seed_historical.py L86-107).
        for cls in CLASSES_FOR_HISTORY:
            seen = rng.randint(20, 40)
            agree_pct = rng.uniform(0.60, 0.92)
            agree = int(seen * agree_pct)
            eno = rng.randint(0, 2)
            await safe.proj_class_unlock.update_one(
                {"class_key": cls},
                {"$set": {
                    "decisions_seen": seen,
                    "agreements": agree,
                    "eno_misses": eno,
                    "unlocked": False,
                }},
                upsert=True,
            )

    # ~ `count` Approval events across signers, byte-identical RNG
    # sequence to the PG seeder — same envelopes, same users, same
    # confidence components.
    signers = ["accounts-a", "accounts-b", "principal", "compliance"]
    for i in range(count):
        cls = rng.choice(CLASSES_FOR_HISTORY)
        agree = rng.random() < 0.75
        components = _components(rng, agree)
        env_id = str(uuid.uuid5(
            uuid.UUID("00000000-0000-4000-8000-000000000fff"),
            f"{tenant_id}:hist:{i}",
        ))
        user = rng.choice(signers)
        role = "accounts" if user.startswith("accounts") else user
        try:
            await append_via_dispatch(
                db, tenant_id, "Approval",
                actor={"kind": "human", "id": user, "name": role},
                payload={
                    "envelope_id": env_id,
                    "class_key": cls,
                    "user_id": user,
                    "role": role,
                    "decision": "approve" if agree else "reject",
                    "confidence": {"components": components,
                                   "scalar": _scalar(components)},
                },
                idempotency_key=(
                    f"appr:{env_id}:{user}:"
                    f"{'approve' if agree else 'reject'}"
                ),
            )
        except (DuplicateKeyError, DuplicateIdempotencyKey):
            pass

    try:
        await append_via_dispatch(
            db, tenant_id, "SeedMarker",
            actor={"kind": "system", "id": "seed", "name": "seed"},
            payload={"kind": "historical_decisions", "count": count},
            idempotency_key=HISTORICAL_KEY,
        )
    except (DuplicateKeyError, DuplicateIdempotencyKey):
        pass
    return {"seeded": True, "count": count}


async def seed_queue_cards_mongo(
    db: AsyncIOMotorDatabase, tenant_id: str, client_id: str | None,
) -> dict:
    if await _already_seeded_mongo(db, tenant_id, CARDS_KEY):
        return {"seeded": False, "reason": "already_seeded"}
    rng = _rng()
    now = datetime.now(timezone.utc)
    cards_out = []
    safe = wrap(db)
    for i, spec in enumerate(CARD_SPECS):
        env_id = str(uuid.uuid5(
            uuid.UUID("00000000-0000-4000-8000-000000000eee"),
            f"{tenant_id}:queue:{i}",
        ))
        hitl_since = now - timedelta(seconds=spec["aged_seconds"])
        components = _components(rng, agree=False)
        conf_scalar = _scalar(components)

        proposal_ref: str
        try:
            proposal = await append_via_dispatch(
                db, tenant_id, "AgentOutputProduced",
                actor={"kind": "system", "id": "agent", "name": "agent"},
                payload={
                    "envelope_id": env_id,
                    "class_key": spec["class_key"],
                    "output": {
                        "summary": f"Proposed action for {spec['class_key']} — awaiting operator review.",
                        "fields": {"mocked_capabilities": ["carrier"]},
                    },
                    "confidence": {"components": components,
                                   "scalar": conf_scalar},
                    "citations": [],
                    "tokens_used": {"input": 0, "output": 0},
                    "mocked_capabilities": ["carrier"],
                },
                idempotency_key=f"proposal:{env_id}",
                mocked=True,
            )
            proposal_ref = proposal["event_id"]
        except (DuplicateKeyError, DuplicateIdempotencyKey):
            async with use_tenant(tenant_id):
                row = await safe.events.find_one(
                    {"idempotency_key": f"proposal:{env_id}"},
                )
            assert row is not None
            proposal_ref = row["event_id"]

        card = await workflow.enqueue(
            db, tenant_id=tenant_id, envelope_id=env_id,
            class_key=spec["class_key"], proposal_ref=proposal_ref,
            matter_id=None, client_id=client_id,
            gate_reason=spec["gate_reason"], confidence=conf_scalar,
            routed_to_role=(
                "accounts" if spec["class_key"] in ("submit_to_market",)
                else "principal"
            ),
            hitl_since=hitl_since,
        )
        cards_out.append({"envelope_id": card.envelope_id,
                          "class_key": card.class_key})

    try:
        await append_via_dispatch(
            db, tenant_id, "SeedMarker",
            actor={"kind": "system", "id": "seed", "name": "seed"},
            payload={"kind": "queue_cards", "count": len(cards_out)},
            idempotency_key=CARDS_KEY,
        )
    except (DuplicateKeyError, DuplicateIdempotencyKey):
        pass
    return {"seeded": True, "cards": cards_out}
