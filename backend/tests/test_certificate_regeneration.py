"""Phase 11 — certificate regeneration wiring (no LLM required)."""
from __future__ import annotations

import json
import uuid

import pytest

from app.kernel.certificate import hash_token, regenerate_certificate
from app.kernel.endorsement import (
    EndorsementLineageIncomplete,
    execute_endorsement,
)
from app.kernel.trail import append


async def _land_lineage(
    pool, tenant_id: str, envelope_id: str, policy_id: str, user_id: str,
) -> None:
    """Simulate the upstream lineage a real scene run would leave."""
    await append(
        pool, tenant_id, "SubmissionDrafted",
        actor={"kind": "system", "id": "placement", "name": "placement"},
        payload={
            "envelope_id": envelope_id,
            "policy_id": policy_id,
            "submission_summary": "raise PL",
            "structured_submission": {
                "cover_type": "public_liability",
                "requested_changes": [{
                    "field_path": "schedule.limits.public_liability",
                    "operation": "set",
                    "from_value": 10_000_000,
                    "to_value": 20_000_000,
                }],
                "market_targets": ["Fictional Carrier A"],
            },
        },
        idempotency_key=f"t:sub:{envelope_id}",
    )
    await append(
        pool, tenant_id, "Approval",
        actor={"kind": "human", "id": user_id, "name": "principal"},
        payload={"envelope_id": envelope_id, "class_key":
                 "endorsement_execute_instructed_quoted",
                 "user_id": user_id, "role": "principal",
                 "decision": "approve", "policy_id": policy_id},
        idempotency_key=f"t:appr:{envelope_id}",
    )
    await append(
        pool, tenant_id, "payment.advised",
        actor={"kind": "system", "id": "payment_rail", "name": "payment"},
        payload={"envelope_id": envelope_id, "policy_id": policy_id,
                 "payment_id": str(uuid.uuid4()), "mocked": True},
        mocked=True,
        idempotency_key=f"t:pay:{envelope_id}",
    )
    await append(
        pool, tenant_id, "SignatureCompleted",
        actor={"kind": "human", "id": user_id, "name": "client_owner"},
        payload={"envelope_id": envelope_id, "policy_id": policy_id,
                 "signature_id": str(uuid.uuid4()), "mocked": True},
        mocked=True,
        idempotency_key=f"t:sig:{envelope_id}",
    )


async def test_endorsement_executes_and_updates_policy(app_client, scene_seeded) -> None:
    app, _ = app_client
    pool = app.state.pool
    tenant_id = scene_seeded["tenant_id"]
    policy_id = scene_seeded["policy_ids"]["managed"]
    user_id = scene_seeded["principal_id"]
    envelope_id = str(uuid.uuid4())

    await _land_lineage(pool, tenant_id, envelope_id, policy_id, user_id)
    result = await execute_endorsement(
        store=pool, tenant_id=tenant_id, envelope_id=envelope_id,
        policy_id=policy_id,
        approver_actor={"kind": "human", "id": user_id, "name": "principal"},
    )
    assert result["endorsement_id"]
    assert result["schedule_after"]["limits"]["public_liability"] == 20_000_000

    # PolicyUpdated event landed.
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            row = await c.fetchrow(
                "SELECT payload FROM events "
                " WHERE tenant_id=$1::uuid AND type='PolicyUpdated' "
                "   AND payload->>'envelope_id' = $2 "
                " ORDER BY seq DESC LIMIT 1",
                tenant_id, envelope_id,
            )
    assert row is not None
    payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
    assert payload["schedule_after"]["limits"]["public_liability"] == 20_000_000


async def test_endorsement_refuses_without_lineage(app_client, scene_seeded) -> None:
    app, _ = app_client
    with pytest.raises(EndorsementLineageIncomplete):
        await execute_endorsement(
            store=app.state.pool, tenant_id=scene_seeded["tenant_id"],
            envelope_id=str(uuid.uuid4()),
            policy_id=scene_seeded["policy_ids"]["managed"],
            approver_actor={"kind": "system", "id": "test", "name": "test"},
        )


async def test_certificate_regeneration_and_token_hash(app_client, scene_seeded) -> None:
    app, _ = app_client
    tenant_id = scene_seeded["tenant_id"]
    policy_id = scene_seeded["policy_ids"]["managed"]

    cert = await regenerate_certificate(
        pool=app.state.pool, tenant_id=tenant_id, policy_id=policy_id,
        envelope_id=None,
    )
    assert cert["certificate"]["policy_id"] == policy_id
    assert hash_token(cert["raw_token"]) == cert["token_hash"]

    # CertificateRegenerated + VerifierTokenIssued both landed.
    async with app.state.pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            evs = await c.fetch(
                "SELECT type FROM events "
                " WHERE tenant_id=$1::uuid AND type IN "
                "     ('CertificateRegenerated','VerifierTokenIssued') "
                " ORDER BY seq DESC LIMIT 2",
                tenant_id,
            )
    types = {r["type"] for r in evs}
    assert types == {"CertificateRegenerated", "VerifierTokenIssued"}
