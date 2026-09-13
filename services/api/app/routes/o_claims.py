"""Operator-side claim scene endpoints.

- POST /api/o/claims/{claim_id}/status-update
    Operator drafts a status update. The draft flows through
    `try_release()` (phase-10 pipeline). On block/rewrite the response
    contains the offending spans + recommended rewrite so the UI can
    surface them inline. On release, the update projects into
    `proj_claim_status_updates` and a client notification is sent.

- POST /api/o/claims/{claim_id}/resolve
    Marks the claim resolved (status='resolved', resolved_at=NOW()) and
    emits `ClaimResolved`.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth.sessions import Session
from app.kernel.notifications import render_and_send
from app.kernel.release import try_release
from app.kernel.trail import append as trail_append
from app.middleware.session import require_operator_session


router = APIRouter(prefix="/api/o/claims")


class StatusUpdateBody(BaseModel):
    draft: str = Field(..., min_length=1, max_length=4000)


async def _load_claim(conn, tenant_id: str, claim_id: str) -> dict | None:
    row = await conn.fetchrow(
        """
        SELECT claim_id::text, client_id::text, claim_reference, status,
               policy_id::text
          FROM claims
         WHERE tenant_id=$1::uuid AND claim_id=$2::uuid
        """,
        tenant_id, claim_id,
    )
    return dict(row) if row else None


@router.get("/{claim_id}")
async def get_claim_operator(
    claim_id: str, request: Request,
    session: Session = Depends(require_operator_session),
) -> dict[str, Any]:
    """Operator view of one claim — full claim row + timeline.

    Tenant-scoped through app.tenant_id GUC. No user_client_links
    dependency (operator isn't linked to the client entity).
    """
    pool = request.app.state.pool
    tenant_id = session.tenant_id
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            claim = await c.fetchrow(
                """
                SELECT claim_id::text, claim_reference, claim_type, urgency,
                       status, opened_at, resolved_at, description,
                       policy_id::text, client_id::text
                  FROM claims
                 WHERE tenant_id=$1::uuid AND claim_id=$2::uuid
                """,
                tenant_id, claim_id,
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
            "client_id": claim["client_id"],
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


@router.post("/{claim_id}/status-update")
async def status_update(
    claim_id: str, request: Request,
    body: StatusUpdateBody = Body(...),
    session: Session = Depends(require_operator_session),
) -> dict[str, Any]:
    pool = request.app.state.pool
    tenant_id = session.tenant_id
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            claim = await _load_claim(c, tenant_id, claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="claim_not_found")

    envelope_id = str(uuid.uuid4())
    release = await try_release(
        pool=pool, tenant_id=tenant_id,
        draft=body.draft,
        context={
            "matter_kind": "claim",
            "claim_reference": claim["claim_reference"],
            "client_context_summary": claim["client_id"],
        },
        author_agent="operator",
        matter_id=None,
        envelope_id=envelope_id,
        client_id=claim["client_id"],
        mocked_channels_used=[],
    )

    # 1. Block or rewrite — surface spans + rewrite to the UI, no notify.
    if release.verdict == "coverage_guard_block":
        return {
            "released": False,
            "verdict": "coverage_guard_block",
            "reason": release.reason,
            "blocked_spans": release.spans,
            "recommended_rewrite": None,
            "guard_visible": True,
        }
    if release.verdict == "rewrite":
        return {
            "released": False,
            "verdict": "rewrite",
            "reason": "compliance classifier requested a rewrite",
            "blocked_spans": [],
            "recommended_rewrite": release.recommended_rewrite,
            "guard_visible": True,
        }
    if release.verdict == "hitl":
        return {
            "released": False,
            "verdict": "hitl",
            "reason": "routed to compliance HITL",
            "guard_visible": True,
        }

    # 2. Released — project + notify.
    released_ev_id = release.event.event_id if release.event else None
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            await c.execute(
                """
                INSERT INTO proj_claim_status_updates
                  (claim_id, tenant_id, event_id, ts, kind, summary, mocked)
                VALUES ($1::uuid, $2::uuid, $3::uuid, NOW(),
                        'operator_update', $4, FALSE)
                """,
                claim_id, tenant_id, released_ev_id, body.draft,
            )
            recipient_row = await c.fetchrow(
                "SELECT u.email FROM users u "
                "JOIN user_client_links l ON l.user_id = u.user_id "
                "WHERE l.client_id=$1::uuid AND l.tenant_id=$2::uuid "
                "LIMIT 1",
                claim["client_id"], tenant_id,
            )
            brk_row = await c.fetchrow(
                "SELECT display_name FROM brokerages "
                "WHERE tenant_id=$1::uuid LIMIT 1", tenant_id,
            )

    notif = await render_and_send(
        pool=pool, tenant_id=tenant_id, kind="claim_status_update",
        payload={
            "claim_reference": claim["claim_reference"],
            "summary": body.draft,
            "brokerage_display": brk_row["display_name"] if brk_row else None,
        },
        recipient={
            "client_id": claim["client_id"],
            "email": recipient_row["email"] if recipient_row else None,
        },
        source_event_id=released_ev_id,
    )

    return {
        "released": True,
        "verdict": "release",
        "event_id": released_ev_id,
        "notification": {
            "dev_mode": notif["dev_mode"],
            "subject": notif["subject"],
        },
        "guard_visible": False,
    }


@router.post("/{claim_id}/resolve")
async def resolve_claim(
    claim_id: str, request: Request,
    session: Session = Depends(require_operator_session),
) -> dict[str, Any]:
    pool = request.app.state.pool
    tenant_id = session.tenant_id
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            claim = await _load_claim(c, tenant_id, claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="claim_not_found")

    ev = await trail_append(
        pool, tenant_id, "ClaimResolved",
        actor={"kind": "human", "id": session.user_id, "name": session.active_role},
        payload={
            "claim_id": claim_id,
            "claim_reference": claim["claim_reference"],
            "status": "resolved",
        },
        idempotency_key=f"claim:resolved:{claim_id}",
    )
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            await c.execute(
                "UPDATE claims SET status='resolved', resolved_at=NOW() "
                " WHERE tenant_id=$1::uuid AND claim_id=$2::uuid",
                tenant_id, claim_id,
            )
            await c.execute(
                """
                INSERT INTO proj_claim_status_updates
                  (claim_id, tenant_id, event_id, ts, kind, summary, mocked)
                VALUES ($1::uuid, $2::uuid, $3::uuid, NOW(),
                        'resolved', 'Claim resolved.', FALSE)
                """,
                claim_id, tenant_id, ev.event_id,
            )
    return {"state": "resolved", "event_id": ev.event_id}
