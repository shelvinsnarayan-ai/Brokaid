"""Two-person rule for `trust_money` HITL resolution.

An accounts-role user submits an Approval on a HITL envelope. This
service:

  1. Records the Approval as a ledger event with idempotency_key
     `appr:{envelope_id}:{user_id}` — same user re-submitting is a
     duplicate signer, not a second signer.
  2. Reads all Approvals for the envelope, filters to role=='accounts',
     counts distinct user_ids.
  3. ≥ 2 distinct accounts signers → emit `TwoPersonRuleSatisfied` and
     return "satisfied". Idempotent via `2pr:satisfied:{envelope_id}`.
  4. Otherwise → emit `TwoPersonRulePending` (with `duplicate_signer`
     flag when appropriate) and return "pending".

A single principal Approval on a `trust_money` envelope stays pending.
A single accounts Approval on a `trust_money` envelope stays pending.
Two Approvals from the same accounts user stay pending. Only two
distinct accounts users satisfy.
"""
from __future__ import annotations

import json
from typing import Literal

import asyncpg

from app.kernel.trail import append


ACTOR = {"kind": "system", "id": "two-person-rule", "name": "two-person"}


async def record_approval(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    envelope_id: str,
    class_key: str,
    user_id: str,
    role: str,
    edits: dict | None = None,
    reason: str | None = None,
) -> Literal["satisfied", "pending"]:
    """Record an Approval and evaluate the two-person rule.

    Only classes that require the two-person rule use this path.
    Currently: `trust_money`.
    """
    # 1. Emit Approval (idempotent per envelope+user).
    duplicate_signer = False
    try:
        await append(
            pool, tenant_id, "Approval",
            actor={"kind": "human", "id": user_id, "name": role},
            payload={
                "envelope_id": envelope_id,
                "class_key": class_key,
                "user_id": user_id,
                "role": role,
                "edits": edits or {},
                "reason": reason,
                "decision": "approve",
            },
            idempotency_key=f"appr:{envelope_id}:{user_id}",
        )
    except asyncpg.UniqueViolationError:
        duplicate_signer = True

    # 2. Read all Approvals for this envelope in the tenant.
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            rows = await c.fetch(
                """
                SELECT payload FROM events
                 WHERE tenant_id = $1::uuid
                   AND type = 'Approval'
                   AND payload->>'envelope_id' = $2
                """,
                tenant_id, envelope_id,
            )

    approvals = []
    for r in rows:
        p = r["payload"]
        approvals.append(p if isinstance(p, dict) else json.loads(p))
    accounts_signers = {a["user_id"] for a in approvals if a.get("role") == "accounts"}

    if len(accounts_signers) >= 2:
        try:
            await append(
                pool, tenant_id, "TwoPersonRuleSatisfied",
                actor=ACTOR,
                payload={
                    "envelope_id": envelope_id,
                    "class_key": class_key,
                    "signers": sorted(accounts_signers),
                },
                idempotency_key=f"2pr:satisfied:{envelope_id}",
            )
        except asyncpg.UniqueViolationError:
            pass
        return "satisfied"

    # Pending — emit an observation. Idempotency key includes user_id so a
    # duplicate signer produces exactly one pending event per attempt.
    await append(
        pool, tenant_id, "TwoPersonRulePending",
        actor=ACTOR,
        payload={
            "envelope_id": envelope_id,
            "class_key": class_key,
            "signer_user_id": user_id,
            "signer_role": role,
            "duplicate_signer": duplicate_signer,
            "distinct_accounts_signers": sorted(accounts_signers),
        },
        idempotency_key=(
            f"2pr:pending:{envelope_id}:{user_id}:"
            f"{'dup' if duplicate_signer else 'new'}"
        ),
    )
    return "pending"
