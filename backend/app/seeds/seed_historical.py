"""Phase-7 seed: historical AgentProposal/HumanDecision pairs + 5
pending queue cards for the operator demo.

Idempotent. Deterministic via a seeded RNG. All fictional.
"""
from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone


from app.kernel import queue
from app.kernel.trail import append


HISTORICAL_KEY = "seed:historical_decisions:v1"
CARDS_KEY = "seed:queue_cards:v1"
SEED_RNG = 20261101   # deterministic
CLASSES_FOR_HISTORY = [
    "structure_risk", "endorsement_standard", "claim_admin",
    "certificate_issue", "renewal_pack_unchanged", "comms_service",
    "request_info", "submit_to_market",
    # Phase 14: two more classes so the shadow-mode dashboard has ≥10 rows.
    "comms_advisory",
    "endorsement_execute_instructed_quoted",
]


def _rng() -> random.Random:
    return random.Random(SEED_RNG)


def _components(rng: random.Random, agree: bool) -> dict:
    """Confidence components; agreement pulls scalars up above 0.85."""
    if agree:
        base = rng.uniform(0.88, 0.98)
    else:
        base = rng.uniform(0.70, 0.86)
    return {
        "schema_validity":   round(base + rng.uniform(-0.02, 0.02), 3),
        "completeness":      round(base + rng.uniform(-0.03, 0.03), 3),
        "citation_existence": round(base + rng.uniform(-0.04, 0.04), 3),
        "class_agreement":   round(base + rng.uniform(-0.03, 0.03), 3),
    }


def _scalar(components: dict) -> float:
    return round(min(components.values()), 3)


async def _already_seeded(pool: object, tenant_id: str, key: str) -> bool:
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            row = await c.fetchrow(
                "SELECT 1 FROM events "
                " WHERE tenant_id = $1::uuid AND idempotency_key = $2",
                tenant_id, key,
            )
    return bool(row)


async def seed_historical_decisions(
    pool: object, tenant_id: str, *, count: int = 30,
) -> dict:
    """Emit `count` proposal/decision pairs across CLASSES_FOR_HISTORY.

    Each pair updates proj_class_unlock atomically via trail._project()
    which handles ClassUnlocked/Relocked. We keep the aggregate
    agreement rate under 95% per class so nothing auto-unlocks.
    """
    if await _already_seeded(pool, tenant_id, HISTORICAL_KEY):
        return {"seeded": False, "reason": "already_seeded"}

    rng = _rng()
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            # We drive unlock state directly via proj_class_unlock
            # because ClassUnlocked/Relocked projections are logic-heavy
            # and belong to phase 2's unlock module. Set realistic totals
            # per class (agreement 60-90%, never ≥ 95%).
            for cls in CLASSES_FOR_HISTORY:
                seen = rng.randint(20, 40)
                agree_pct = rng.uniform(0.60, 0.92)
                agree = int(seen * agree_pct)
                eno = rng.randint(0, 2)
                await c.execute(
                    """
                    INSERT INTO proj_class_unlock
                      (tenant_id, class_key, decisions_seen, agreements,
                       eno_misses, unlocked)
                    VALUES ($1::uuid, $2, $3, $4, $5, FALSE)
                    ON CONFLICT (tenant_id, class_key) DO UPDATE SET
                      decisions_seen = EXCLUDED.decisions_seen,
                      agreements     = EXCLUDED.agreements,
                      eno_misses     = EXCLUDED.eno_misses
                    """,
                    tenant_id, cls, seen, agree, eno,
                )

    # Emit ~ `count` Approval events over the last 7 days so KPI-10
    # (approvals load) has real data. Distinct users across accounts.
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
            await append(
                pool, tenant_id, "Approval",
                actor={"kind": "human", "id": user, "name": role},
                payload={
                    "envelope_id": env_id,
                    "class_key": cls,
                    "user_id": user,
                    "role": role,
                    "decision": "approve" if agree else "reject",
                    "confidence": {"components": components, "scalar": _scalar(components)},
                },
                idempotency_key=f"appr:{env_id}:{user}:{'approve' if agree else 'reject'}",
            )
        except Exception:
            pass

    # Landmark marker so we don't re-seed.
    try:
        await append(
            pool, tenant_id, "SeedMarker",
            actor={"kind": "system", "id": "seed", "name": "seed"},
            payload={"kind": "historical_decisions", "count": count},
            idempotency_key=HISTORICAL_KEY,
        )
    except Exception:
        pass

    return {"seeded": True, "count": count}


CARD_SPECS = [
    # (class_key, sla_offset_seconds relative to now, gate_reason)
    # 2 overdue, 2 warn, 1 ok.
    {"class_key": "certificate_issue", "aged_seconds": 5 * 3600,
     "gate_reason": "non_standard_wording"},
    {"class_key": "structure_risk", "aged_seconds": 5 * 3600,
     "gate_reason": "hitl_predicate matched"},
    {"class_key": "claim_admin", "aged_seconds": 7 * 3600,
     "gate_reason": "coverage_language_detected"},
    {"class_key": "endorsement_execute_instructed_quoted",
     "aged_seconds": 23 * 3600,
     "gate_reason": "ambiguous_reply"},
    {"class_key": "submit_to_market", "aged_seconds": 60,
     "gate_reason": "new_business"},
]


async def seed_queue_cards(
    pool: object, tenant_id: str, client_id: str | None,
) -> dict:
    if await _already_seeded(pool, tenant_id, CARDS_KEY):
        return {"seeded": False, "reason": "already_seeded"}
    rng = _rng()
    now = datetime.now(timezone.utc)
    cards_out = []
    for i, spec in enumerate(CARD_SPECS):
        env_id = str(uuid.uuid5(
            uuid.UUID("00000000-0000-4000-8000-000000000eee"),
            f"{tenant_id}:queue:{i}",
        ))
        hitl_since = now - timedelta(seconds=spec["aged_seconds"])
        components = _components(rng, agree=False)
        conf_scalar = _scalar(components)

        # Emit AgentOutputProduced as the proposal_ref.
        try:
            proposal = await append(
                pool, tenant_id, "AgentOutputProduced",
                actor={"kind": "system", "id": "agent", "name": "agent"},
                payload={
                    "envelope_id": env_id,
                    "class_key": spec["class_key"],
                    "output": {
                        "summary": f"Proposed action for {spec['class_key']} — awaiting operator review.",
                        "fields": {"mocked_capabilities": ["carrier"]},
                    },
                    "confidence": {"components": components, "scalar": conf_scalar},
                    "citations": [],
                    "tokens_used": {"input": 0, "output": 0},
                    "mocked_capabilities": ["carrier"],
                },
                idempotency_key=f"proposal:{env_id}",
                mocked=True,
            )
        except Exception:
            # Fallback — find existing.
            async with pool.acquire() as c:
                await c.execute(
                    "SELECT set_config('app.tenant_id', $1, true)", tenant_id
                )
                row = await c.fetchrow(
                    "SELECT event_id::text AS event_id FROM events "
                    " WHERE tenant_id = $1::uuid AND idempotency_key = $2",
                    tenant_id, f"proposal:{env_id}",
                )
            proposal_ref = row["event_id"]
        else:
            proposal_ref = proposal.event_id

        card = await queue.enqueue(
            pool, tenant_id=tenant_id, envelope_id=env_id,
            class_key=spec["class_key"], proposal_ref=proposal_ref,
            matter_id=None, client_id=client_id,
            gate_reason=spec["gate_reason"], confidence=conf_scalar,
            routed_to_role="accounts" if spec["class_key"] in ("submit_to_market",)
                           else "principal",
            hitl_since=hitl_since,
        )
        cards_out.append({"envelope_id": card.envelope_id,
                          "class_key": card.class_key})

    try:
        await append(
            pool, tenant_id, "SeedMarker",
            actor={"kind": "system", "id": "seed", "name": "seed"},
            payload={"kind": "queue_cards", "count": len(cards_out)},
            idempotency_key=CARDS_KEY,
        )
    except Exception:
        pass
    return {"seeded": True, "cards": cards_out}
