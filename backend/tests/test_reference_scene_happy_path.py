"""Phase 11 — reference scene happy-path e2e (W4 + W5).

Runs the full client-initiated raise-limit flow end-to-end against
the LIVE intAIke + plAIce agents and the MOCKED carrier / payment /
e-sign channels.

Marker: requires_llm_key. Skipped without EMERGENT_LLM_KEY.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

from app.kernel.certificate import regenerate_certificate
from app.kernel.endorsement import execute_endorsement
from app.kernel.mocks import e_sign, payment_rail
from app.kernel.mocks.carrier_channel import submit_endorsement
from app.kernel.queue import enqueue as queue_enqueue
from app.kernel.trail import append


pytestmark = [
    pytest.mark.requires_llm_key,
    pytest.mark.skipif(
        not os.environ.get("EMERGENT_LLM_KEY"),
        reason="phase 11 reference scene requires EMERGENT_LLM_KEY",
    ),
]


# The expected event trace, in order, with expected actor.kind.
EXPECTED_TRACE: list[tuple[str, str]] = [
    ("ClientChangeRequested",   "human"),
    ("AgentInvocationStarted",  "system"),
    ("AgentOutputProduced",     "system"),
    ("AgentInvocationCompleted","system"),
    ("AgentInvocationStarted",  "system"),
    ("AgentOutputProduced",     "system"),
    ("AgentInvocationCompleted","system"),
    ("SubmissionDrafted",       "system"),
    ("CarrierQuoted",           "system"),
    ("EndorsementQuoteReady",   "system"),
    ("QueueCardEnqueued",       "system"),
    ("Approval",                "human"),
    ("payment.advised",         "human"),
    ("SignatureRequested",      "system"),
    ("SignatureCompleted",      "human"),
    ("EndorsementExecuted",     "system"),
    ("PolicyUpdated",           "system"),
    ("CertificateRegenerated",  "system"),
    ("VerifierTokenIssued",     "system"),
]


async def test_reference_scene_happy_path(app_client, scene_seeded) -> None:
    app, client = app_client
    tenant_id = scene_seeded["tenant_id"]
    policy_id = scene_seeded["policy_ids"]["managed"]
    client_id = scene_seeded["client_id"]
    user_id = scene_seeded["principal_id"]

    # --- Dev-mode session as the client owner (Kai) for the scene actions.
    #     The scene endpoints are under /c/... and require a client session.
    resp = await client.post(
        "/api/auth/dev/impersonate", json={"email": "owner@fictionalclient.example"},
    )
    assert resp.status_code == 200, resp.text

    started = time.monotonic()

    # --- 1. Client submits a change request (intAIke → plAIce → carrier) --
    r = await client.post(
        f"/api/c/matters/{policy_id}/request-change",
        json={"raw_message":
              "We took on a bigger contract. Please raise our public liability from $10M to $20M."},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "awaiting_operator", body
    envelope_id = body["envelope_id"]
    quote_id = body["quote"]["quote_id"]

    pool = app.state.pool

    # --- 2. Operator approval (simulated at ledger boundary) ---------------
    await append(
        pool, tenant_id, "Approval",
        actor={"kind": "human", "id": user_id, "name": "principal"},
        payload={
            "envelope_id": envelope_id,
            "class_key": "endorsement_execute_instructed_quoted",
            "user_id": user_id, "role": "principal",
            "decision": "approve", "edits": {}, "reason": None,
            "policy_id": policy_id,
        },
        idempotency_key=f"appr:{envelope_id}:{user_id}:approve",
    )

    # --- 3. Client sees MOCKED BPAY/EFT details, then advises payment -----
    r = await client.get(f"/api/c/matters/{policy_id}/pay/{envelope_id}")
    assert r.status_code == 200, r.text
    assert r.json()["payment_details"]["mocked"] is True
    r = await client.post(f"/api/c/matters/{policy_id}/paid/{envelope_id}")
    assert r.status_code == 200, r.text
    assert r.json()["ledger_event"]["type"] == "payment.advised"

    # --- 4. Client signs (MOCKED) -----------------------------------------
    r = await client.post(f"/api/c/matters/{policy_id}/sign/{envelope_id}")
    assert r.status_code == 200, r.text
    assert r.json()["signature"]["mocked"] is True

    # --- 5. Finalise: endorsement + certificate ---------------------------
    r = await client.post(f"/api/c/matters/{policy_id}/finalise/{envelope_id}")
    assert r.status_code == 200, r.text
    fin = r.json()
    assert fin["state"] == "certificate_ready"
    cert = fin["certificate"]
    assert cert["holder_display"]
    assert cert["insurer_display"] in ("Fictional Carrier A", "Fictional Carrier B")
    assert cert["mocked_insurer"] is True
    raw_token = fin["verifier_token"]
    assert len(raw_token) >= 30

    elapsed = time.monotonic() - started
    assert elapsed < 60.0, f"scene too slow: {elapsed:.1f}s"

    # --- 6. Ledger trace: expected types + actor kinds in order -----------
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            rows = await c.fetch(
                """
                SELECT seq, type, actor FROM events
                 WHERE tenant_id=$1::uuid
                   AND payload->>'envelope_id' = $2
                 ORDER BY seq ASC
                """,
                tenant_id, envelope_id,
            )
    traced = [
        (r["type"], (json.loads(r["actor"]) if isinstance(r["actor"], str) else r["actor"])["kind"])
        for r in rows
    ]
    _assert_ordered_subsequence(traced, EXPECTED_TRACE)


def _assert_ordered_subsequence(
    trace: list[tuple[str, str]], expected: list[tuple[str, str]],
) -> None:
    """Assert `expected` appears as a subsequence of `trace`, preserving order."""
    it = iter(trace)
    idx = 0
    missing: list[tuple[str, str]] = []
    for want in expected:
        for got in it:
            if got == want:
                idx += 1
                break
        else:
            missing.append(want)
    assert not missing, (
        f"missing events in expected order: {missing}\n"
        f"trace was:\n  " + "\n  ".join(f"{t[0]} ({t[1]})" for t in trace)
    )
