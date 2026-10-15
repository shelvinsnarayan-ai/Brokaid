"""Phase 4 — Compliance block / clear + gAIte enforcement."""
from __future__ import annotations

import uuid

import pytest

from app.kernel import compliance
from app.kernel.gate.confidence import Confidence
from app.kernel.gate.gate import evaluate
from app.kernel.trail import read_events

pytestmark = pytest.mark.asyncio


def _conf(v: float) -> Confidence:
    return Confidence(
        components={"schema_validity": v, "completeness": v,
                    "citation_existence": v, "class_agreement": v},
        scalar=v,
    )


async def test_compliance_block_forces_gate_block(seeded, pool):
    tenant_id = seeded["tenant_id"]
    matter_id = str(uuid.uuid4())

    # Baseline — no block, structure_risk with hi conf autonomously OK.
    d = evaluate("structure_risk", {}, _conf(1.0), {})
    assert d.decision == "AUTONOMOUS_OK"

    # Add a block on the matter.
    block_id = await compliance.add_block(
        pool, tenant_id=tenant_id,
        target_kind="matter", target_id=matter_id,
        reason="KYC not verified",
        actor={"kind": "human", "id": "compliance-user", "name": "Fin"},
    )

    # Simulate the brain flow: lookup active block, pass to gate.
    active = await compliance.active_block_id(
        pool, tenant_id, "matter", matter_id
    )
    assert active == block_id

    d = evaluate(
        "structure_risk", {}, _conf(1.0), {},
        compliance_block_id=active,
    )
    assert d.decision == "BLOCK"
    assert d.reason == f"compliance_block:{block_id}"

    # Even for an H class the block reason is the compliance block.
    d = evaluate(
        "trust_money", {}, _conf(0.5), {},
        compliance_block_id=active,
    )
    assert d.decision == "BLOCK"


async def test_non_compliance_hit_block_endpoint_403(app_client, seeded):
    _, client = app_client
    # Impersonate a principal.
    r = await client.post(
        "/api/auth/dev/impersonate",
        json={"email": "principal@harbourline.example"},
    )
    assert r.status_code == 200
    r = await client.post(
        "/api/o/compliance/block",
        json={"target_kind": "matter", "target_id": "m1", "reason": "x"},
    )
    assert r.status_code == 403


async def test_compliance_user_can_block_and_clear(app_client, seeded):
    app, client = app_client
    matter_id = str(uuid.uuid4())

    r = await client.post(
        "/api/auth/dev/impersonate",
        json={"email": "compliance@harbourline.example"},
    )
    assert r.status_code == 200

    # Block.
    r = await client.post(
        "/api/o/compliance/block",
        json={"target_kind": "matter", "target_id": matter_id,
              "reason": "docs missing"},
    )
    assert r.status_code == 200
    block_id = r.json()["block_id"]

    active = await compliance.active_block_id(
        app.state.pool, seeded["tenant_id"], "matter", matter_id
    )
    assert active == block_id

    # Clear.
    r = await client.post(
        f"/api/o/compliance/clear/{block_id}",
        json={"reason": "docs received"},
    )
    assert r.status_code == 200

    active = await compliance.active_block_id(
        app.state.pool, seeded["tenant_id"], "matter", matter_id
    )
    assert active is None

    # ComplianceBlockAdded event still on the ledger (append-only).
    _, events = await read_events(app.state.pool, "burmac")
    added = [e for e in events if e.type == "ComplianceBlockAdded"]
    cleared = [e for e in events if e.type == "ComplianceBlockCleared"]
    assert len(added) == 1
    assert len(cleared) == 1
    assert added[0].payload["block_id"] == block_id
