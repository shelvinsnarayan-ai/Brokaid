"""Client-facing claim scene endpoints.

Flow:
  POST /api/c/claims                → FNOL: submit + triage + lodge + notify
  GET  /api/c/claims                → list claims for the linked client
  GET  /api/c/claims/{claim_id}     → detail + timeline
  GET  /api/c/notifications         → notifications tray (recent)
  POST /api/c/notifications/{id}/read → mark read
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth.sessions import Session
from app.kernel.agents.claims_stub import DeterministicClaimsStubProvider
from app.kernel.brain import Brain
from app.kernel.contracts.models import Actor, ContextEnvelope, InvocationBudget
from app.kernel.mocks.insurer_lodgement import lodge_claim
from app.kernel.notifications import render_and_send
from app.kernel.queue import enqueue as queue_enqueue
from app.kernel.trail import append as trail_append
from app.middleware.session import require_session


router = APIRouter(prefix="/api/c")


class FNOLBody(BaseModel):
    incident_date: str | None = Field(default=None, description="YYYY-MM-DD")
    description: str = Field(..., min_length=8, max_length=4000)
    policy_id: str | None = None


async def _load_client(pool, tenant_id: str, session: Session) -> dict:
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            row = await c.fetchrow(
                """
                SELECT cl.client_id::text AS client_id, cl.display_name,
                       b.display_name AS brokerage_display
                  FROM user_client_links l
                  JOIN clients cl ON cl.client_id = l.client_id
             LEFT JOIN brokerages b ON b.tenant_id = cl.tenant_id
                 WHERE l.user_id = $1::uuid AND cl.tenant_id = $2::uuid
                 LIMIT 1
                """,
                session.user_id, tenant_id,
            )
    if not row:
        raise HTTPException(status_code=404, detail="client_not_linked")
    return dict(row)


async def _next_reference(conn, tenant_id: str) -> str:
    year = datetime.now(timezone.utc).year
    row = await conn.fetchrow(
        """
        SELECT COALESCE(
          MAX(NULLIF(regexp_replace(claim_reference, '\\D', '', 'g'), '')::bigint), 0
        ) AS n
          FROM claims
         WHERE tenant_id=$1::uuid
           AND claim_reference LIKE $2
        """,
        tenant_id, f"CLM-{year}-%",
    )
    n = int(row["n"] or 0) + 1
    return f"CLM-{year}-{n:06d}"


def _parse_iso_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


@router.post("/claims")
async def submit_fnol(
    request: Request,
    body: FNOLBody = Body(...),
    session: Session = Depends(require_session),
) -> dict[str, Any]:
    """FNOL entry. Runs the full claim scene end-to-end."""
    pool = request.app.state.pool
    tenant_id = session.tenant_id
    client = await _load_client(pool, tenant_id, session)

    envelope_id = uuid.uuid4()
    incident_dt = _parse_iso_date(body.incident_date)

    # 1. ClientRequestReceived
    await trail_append(
        pool, tenant_id, "ClientRequestReceived",
        actor={"kind": "human", "id": session.user_id, "name": "client_owner"},
        payload={
            "envelope_id": str(envelope_id),
            "matter_kind": "claim",
            "client_id": client["client_id"],
            "raw_message": body.description,
            "policy_id": body.policy_id,
            "incident_date": body.incident_date,
        },
        idempotency_key=f"claim:req:{envelope_id}",
    )

    # 2. clAIms stub triage via brAIn.
    env = ContextEnvelope(
        envelope_id=envelope_id,
        tenant_id=uuid.UUID(tenant_id),
        matter_id=None,
        client_id=uuid.UUID(client["client_id"]),
        class_key="claim_admin",
        intent="triage FNOL",
        inputs={
            "raw_fnol_message": body.description,
            "matter_kind": "claim",
            "client_context_summary": client["display_name"],
        },
        context_events=[],
        risk_schema_refs=[],
        tenant_config={},
        budget=InvocationBudget(
            max_input_tokens=2000, max_output_tokens=800, max_wall_ms=5000,
        ),
        caller=Actor(kind="human", id=session.user_id, name="client_owner"),
        ts=datetime.now(timezone.utc),
    )
    brain = Brain(pool, resolver=lambda name: DeterministicClaimsStubProvider())
    triage_out = await brain.invoke(env, "claims")
    triage = triage_out.payload or {}

    # 3. ClaimTriaged
    await trail_append(
        pool, tenant_id, "ClaimTriaged",
        actor={"kind": "system", "id": "claims", "name": "claims"},
        payload={
            "envelope_id": str(envelope_id),
            "client_id": client["client_id"],
            "claim_type": triage.get("claim_type"),
            "urgency": triage.get("urgency"),
            "key_facts": triage.get("key_facts") or [],
            "requires_coverage_check": bool(triage.get("requires_coverage_check")),
            "structured_summary": triage.get("structured_summary"),
        },
        idempotency_key=f"claim:triaged:{envelope_id}",
    )

    # 4. Persist the claim row + emit ClaimCreated in one transaction.
    claim_id = str(uuid.uuid4())
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            claim_ref = await _next_reference(c, tenant_id)
            created_ev = await trail_append(
                pool, tenant_id, "ClaimCreated",
                actor={"kind": "system", "id": "claims", "name": "claims"},
                payload={
                    "envelope_id": str(envelope_id),
                    "claim_id": claim_id,
                    "claim_reference": claim_ref,
                    "client_id": client["client_id"],
                    "policy_id": body.policy_id,
                    "claim_type": triage.get("claim_type"),
                    "urgency": triage.get("urgency"),
                    "description": body.description,
                    "incident_date": body.incident_date,
                    "status": "triaged",
                },
                idempotency_key=f"claim:created:{claim_id}",
                conn=c,
            )
            await c.execute(
                """
                INSERT INTO claims
                  (claim_id, tenant_id, client_id, policy_id, claim_reference,
                   incident_date, description, claim_type, urgency,
                   status, opened_by_event)
                VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5,
                        $6, $7, $8, $9, 'triaged', $10::uuid)
                """,
                claim_id, tenant_id, client["client_id"],
                (body.policy_id or None), claim_ref,
                incident_dt, body.description,
                triage.get("claim_type"), triage.get("urgency"),
                created_ev.event_id,
            )
            await c.execute(
                """
                INSERT INTO proj_claim_status_updates
                  (claim_id, tenant_id, event_id, ts, kind, summary, mocked)
                VALUES ($1::uuid, $2::uuid, $3::uuid, NOW(),
                        'triaged', $4, FALSE)
                """,
                claim_id, tenant_id, created_ev.event_id,
                triage.get("structured_summary") or "Triage complete.",
            )

    # 5. MOCKED insurer lodgement.
    lodgement_req_ev = await trail_append(
        pool, tenant_id, "InsurerLodgementRequested",
        actor={"kind": "system", "id": "insurer-channel", "name": "insurer"},
        payload={
            "envelope_id": str(envelope_id),
            "claim_id": claim_id,
            "claim_reference": claim_ref,
            "mocked": True,
        },
        mocked=True,
        idempotency_key=f"lodgement:req:{claim_id}",
    )
    lodgement = await lodge_claim({
        "claim_id": claim_id,
        "claim_reference": claim_ref,
        "claim_type": triage.get("claim_type"),
        "description": body.description,
    })
    ack_ev = await trail_append(
        pool, tenant_id, "InsurerLodgementAck",
        actor={"kind": "system", "id": "insurer-channel", "name": "insurer"},
        payload={
            "envelope_id": str(envelope_id),
            "claim_id": claim_id,
            "claim_reference": claim_ref,
            "insurer_display": lodgement["insurer_display"],
            "insurer_reference": lodgement["insurer_reference"],
            "lodgement_id": lodgement["lodgement_id"],
            "mocked": True,
        },
        mocked=True,
        idempotency_key=f"lodgement:ack:{claim_id}",
    )
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            await c.execute("UPDATE claims SET status='lodged' WHERE claim_id=$1::uuid", claim_id)
            await c.execute(
                """
                INSERT INTO proj_claim_status_updates
                  (claim_id, tenant_id, event_id, ts, kind, summary, mocked)
                VALUES ($1::uuid, $2::uuid, $3::uuid, NOW(),
                        'lodged', $4, TRUE)
                """,
                claim_id, tenant_id, ack_ev.event_id,
                f"Lodged with {lodgement['insurer_display']} · ref {lodgement['insurer_reference']}",
            )

    # 6. Enqueue an operator card if urgency=critical or coverage question.
    if triage.get("urgency") == "critical" or triage.get("requires_coverage_check"):
        try:
            await queue_enqueue(
                pool,
                tenant_id=tenant_id,
                envelope_id=str(envelope_id),
                class_key="claim_admin",
                proposal_ref=created_ev.event_id,
                matter_id=None,
                client_id=client["client_id"],
                gate_reason=(
                    "coverage_check_required"
                    if triage.get("requires_coverage_check")
                    else "urgency_critical"
                ),
                confidence=0.7,
                routed_to_role="principal",
            )
            await trail_append(
                pool, tenant_id, "QueueCardEnqueued",
                actor={"kind": "system", "id": "queue", "name": "queue"},
                payload={
                    "envelope_id": str(envelope_id),
                    "claim_id": claim_id,
                    "class_key": "claim_admin",
                },
                idempotency_key=f"claim:queued:{envelope_id}",
            )
        except Exception:
            pass  # projection is best-effort

    # 7. Notify.
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            recipient_row = await c.fetchrow(
                "SELECT u.email FROM users u "
                "JOIN user_client_links l ON l.user_id = u.user_id "
                "WHERE l.client_id=$1::uuid AND l.tenant_id=$2::uuid "
                "LIMIT 1",
                client["client_id"], tenant_id,
            )
    recipient_email = recipient_row["email"] if recipient_row else None

    notif = await render_and_send(
        pool=pool, tenant_id=tenant_id, kind="claim_lodged",
        payload={
            "claim_reference": claim_ref,
            "insurer_display": lodgement["insurer_display"],
            "client_display": client["display_name"],
            "brokerage_display": client.get("brokerage_display"),
            "summary": (
                f"Your claim has been lodged with "
                f"{lodgement['insurer_display']} "
                f"(reference {lodgement['insurer_reference']}). "
                "We'll update you as it moves."
            ),
        },
        recipient={"client_id": client["client_id"], "email": recipient_email},
        source_event_id=ack_ev.event_id,
    )

    return {
        "state": "lodged",
        "claim_id": claim_id,
        "claim_reference": claim_ref,
        "envelope_id": str(envelope_id),
        "triage": triage,
        "lodgement": {
            "insurer_display": lodgement["insurer_display"],
            "insurer_reference": lodgement["insurer_reference"],
            "mocked": True,
        },
        "notification": {
            "dev_mode": notif["dev_mode"],
            "subject": notif["subject"],
        },
        "next_steps": [
            "Watch this claim's timeline for insurer updates.",
            "You can message your broker any time via the Ask channel.",
        ],
    }


@router.get("/claims")
async def list_claims(
    request: Request,
    session: Session = Depends(require_session),
) -> dict[str, Any]:
    pool = request.app.state.pool
    tenant_id = session.tenant_id
    client = await _load_client(pool, tenant_id, session)
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            rows = await c.fetch(
                """
                SELECT claim_id::text, claim_reference, claim_type, urgency,
                       status, opened_at, resolved_at, description
                  FROM claims
                 WHERE tenant_id=$1::uuid AND client_id=$2::uuid
                 ORDER BY opened_at DESC LIMIT 100
                """,
                tenant_id, client["client_id"],
            )
    return {
        "claims": [
            {
                "claim_id": r["claim_id"],
                "claim_reference": r["claim_reference"],
                "claim_type": r["claim_type"],
                "urgency": r["urgency"],
                "status": r["status"],
                "opened_at": r["opened_at"].isoformat(timespec="microseconds"),
                "resolved_at": (
                    r["resolved_at"].isoformat(timespec="microseconds")
                    if r["resolved_at"] else None
                ),
                "description_preview": (r["description"] or "")[:140],
            }
            for r in rows
        ],
    }


@router.get("/claims/{claim_id}")
async def get_claim(
    claim_id: str, request: Request,
    session: Session = Depends(require_session),
) -> dict[str, Any]:
    pool = request.app.state.pool
    tenant_id = session.tenant_id
    client = await _load_client(pool, tenant_id, session)
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            claim = await c.fetchrow(
                """
                SELECT claim_id::text, claim_reference, claim_type, urgency,
                       status, opened_at, resolved_at, description, policy_id::text
                  FROM claims
                 WHERE tenant_id=$1::uuid AND client_id=$2::uuid
                   AND claim_id=$3::uuid
                """,
                tenant_id, client["client_id"], claim_id,
            )
            if not claim:
                raise HTTPException(status_code=404, detail="claim_not_found")
            timeline_rows = await c.fetch(
                """
                SELECT seq, ts, kind, summary, mocked
                  FROM proj_claim_status_updates
                 WHERE tenant_id=$1::uuid AND claim_id=$2::uuid
                 ORDER BY seq DESC
                """,
                tenant_id, claim_id,
            )
    return {
        "claim": {
            "claim_id": claim["claim_id"],
            "claim_reference": claim["claim_reference"],
            "claim_type": claim["claim_type"],
            "urgency": claim["urgency"],
            "status": claim["status"],
            "opened_at": claim["opened_at"].isoformat(timespec="microseconds"),
            "resolved_at": (
                claim["resolved_at"].isoformat(timespec="microseconds")
                if claim["resolved_at"] else None
            ),
            "description": claim["description"],
            "policy_id": claim["policy_id"],
        },
        "timeline": [
            {
                "seq": r["seq"],
                "ts": r["ts"].isoformat(timespec="microseconds"),
                "kind": r["kind"],
                "summary": r["summary"],
                "mocked": r["mocked"],
            }
            for r in timeline_rows
        ],
    }


@router.get("/notifications")
async def list_notifications(
    request: Request,
    session: Session = Depends(require_session),
) -> dict[str, Any]:
    pool = request.app.state.pool
    tenant_id = session.tenant_id
    client = await _load_client(pool, tenant_id, session)
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            rows = await c.fetch(
                """
                SELECT notification_id::text, ts, kind, subject, body_text, read_at,
                       source_event_id::text
                  FROM proj_notifications
                 WHERE tenant_id=$1::uuid AND client_id=$2::uuid
                 ORDER BY ts DESC LIMIT 50
                """,
                tenant_id, client["client_id"],
            )
    return {
        "notifications": [
            {
                "notification_id": r["notification_id"],
                "ts": r["ts"].isoformat(timespec="microseconds"),
                "kind": r["kind"],
                "subject": r["subject"],
                "body_text": r["body_text"],
                "read": r["read_at"] is not None,
                "source_event_id": r["source_event_id"],
            }
            for r in rows
        ],
    }
