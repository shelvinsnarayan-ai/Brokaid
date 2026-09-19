"""Queue projector — pending HITL Decision Cards.

Scope for phase 7:
  * SLA deadline map per class_key
  * enqueue()  → INSERT into proj_queue + emit QueueCardEnqueued
  * resolve()  → mark resolved_at, resolved_by_event
  * fetch_pending() / fetch_card() read-side helpers used by routes and
    seed fixtures.

No autonomous consumer of gAIte HITL events yet — the endorsement /
claim scenes (phase 8+) will call `enqueue()` explicitly. This keeps
the projector deterministic and testable now.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


from app.kernel.trail import append


# H classes and money-touching → 30min; others per brief.
SLA_MINUTES: dict[str, int] = {
    # H classes (all money / advisory / legal — tight 30 min)
    "bind_new_or_remarket":       30,
    "comms_advisory":             30,
    "coverage_position":          30,
    "trust_money":                30,
    "complaint_determination":    30,
    "clear_block":                30,
    # Money-touching non-H
    "cancel_refund":              30,
    # Fast lanes
    "structure_risk":            240,   # 4h
    "certificate_issue":         240,   # 4h
    "claim_admin":               480,   # 8h
    # Standard lanes
    "submit_to_market":         1440,   # 24h
    "endorsement_execute_instructed_quoted": 1440,
    # Everything else defaults to 24h
}
DEFAULT_SLA_MINUTES = 1440


def sla_deadline_for(class_key: str, hitl_since: datetime) -> datetime:
    mins = SLA_MINUTES.get(class_key, DEFAULT_SLA_MINUTES)
    return hitl_since + timedelta(minutes=mins)


def sla_state(now: datetime, deadline: datetime | None) -> str:
    """`'ok' | 'warn' | 'overdue'` — warn = within last 20% of window."""
    if deadline is None:
        return "ok"
    if now >= deadline:
        return "overdue"
    remaining = (deadline - now).total_seconds()
    total_window = 3600.0                  # fallback if we can't infer
    if remaining <= total_window * 0.2 or remaining <= 900:  # 15min buffer
        return "warn"
    return "ok"


@dataclass(frozen=True)
class QueueCard:
    envelope_id: str
    class_key: str
    matter_id: str | None
    client_id: str | None
    proposal_ref: str
    hitl_since: datetime
    sla_deadline: datetime | None
    gate_reason: str | None
    confidence: float | None
    routed_to_role: str | None
    resolved_at: datetime | None
    resolved_by_event: str | None


async def enqueue(
    pool: object,
    *,
    tenant_id: str,
    envelope_id: str,
    class_key: str,
    proposal_ref: str,
    matter_id: str | None = None,
    client_id: str | None = None,
    gate_reason: str | None = None,
    confidence: float | None = None,
    routed_to_role: str | None = None,
    hitl_since: datetime | None = None,
) -> QueueCard:
    """Idempotent enqueue. Emits QueueCardEnqueued once."""
    now = hitl_since or datetime.now(timezone.utc)
    deadline = sla_deadline_for(class_key, now)

    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            await c.execute(
                """
                INSERT INTO proj_queue
                  (tenant_id, envelope_id, matter_id, client_id, class_key,
                   proposal_ref, hitl_since, sla_deadline, gate_reason,
                   confidence, routed_to_role)
                VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6::uuid,
                        $7, $8, $9, $10, $11)
                ON CONFLICT (tenant_id, envelope_id) DO NOTHING
                """,
                tenant_id, envelope_id, matter_id, client_id, class_key,
                proposal_ref, now, deadline, gate_reason,
                confidence, routed_to_role,
            )

    try:
        await append(
            pool, tenant_id, "QueueCardEnqueued",
            actor={"kind": "system", "id": "queue", "name": "queue"},
            payload={
                "envelope_id": envelope_id,
                "class_key": class_key,
                "matter_id": matter_id,
                "client_id": client_id,
                "proposal_ref": proposal_ref,
                "hitl_since": now.isoformat(timespec="microseconds"),
                "sla_deadline": deadline.isoformat(timespec="microseconds"),
                "gate_reason": gate_reason,
                "confidence": confidence,
                "routed_to_role": routed_to_role,
            },
            idempotency_key=f"queue:enqueue:{envelope_id}",
        )
    except Exception:
        pass

    return QueueCard(
        envelope_id=envelope_id, class_key=class_key,
        matter_id=matter_id, client_id=client_id,
        proposal_ref=proposal_ref, hitl_since=now,
        sla_deadline=deadline, gate_reason=gate_reason,
        confidence=confidence, routed_to_role=routed_to_role,
        resolved_at=None, resolved_by_event=None,
    )


async def resolve(
    pool: object, *, tenant_id: str, envelope_id: str,
    resolved_by_event: str,
) -> None:
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            await c.execute(
                """
                UPDATE proj_queue
                   SET resolved_at = NOW(),
                       resolved_by_event = $3::uuid
                 WHERE tenant_id = $1::uuid
                   AND envelope_id = $2::uuid
                   AND resolved_at IS NULL
                """,
                tenant_id, envelope_id, resolved_by_event,
            )


def _row_to_dict(r: dict, now: datetime) -> dict[str, Any]:
    deadline = r["sla_deadline"]
    return {
        "envelope_id": str(r["envelope_id"]),
        "class_key": r["class_key"],
        "matter_id": str(r["matter_id"]) if r["matter_id"] else None,
        "client_id": str(r["client_id"]) if r["client_id"] else None,
        "proposal_ref": str(r["proposal_ref"]),
        "hitl_since": r["hitl_since"].isoformat(timespec="microseconds"),
        "sla_deadline": deadline.isoformat(timespec="microseconds") if deadline else None,
        "sla_state": sla_state(now, deadline),
        "gate_reason": r["gate_reason"],
        "confidence": float(r["confidence"]) if r["confidence"] is not None else None,
        "routed_to_role": r["routed_to_role"],
    }


async def fetch_pending(
    pool: object, tenant_id: str
) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            rows = await c.fetch(
                """
                SELECT envelope_id, matter_id, client_id, class_key,
                       proposal_ref, hitl_since, sla_deadline, gate_reason,
                       confidence, routed_to_role
                  FROM proj_queue
                 WHERE tenant_id = $1::uuid AND resolved_at IS NULL
                 ORDER BY (sla_deadline IS NULL) ASC,
                          (sla_deadline <= NOW()) DESC,
                          sla_deadline ASC,
                          hitl_since ASC
                """,
                tenant_id,
            )
    return [_row_to_dict(r, now) for r in rows]


async def fetch_card(
    pool: object, tenant_id: str, envelope_id: str
) -> dict[str, Any] | None:
    now = datetime.now(timezone.utc)
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            row = await c.fetchrow(
                """
                SELECT envelope_id, matter_id, client_id, class_key,
                       proposal_ref, hitl_since, sla_deadline, gate_reason,
                       confidence, routed_to_role
                  FROM proj_queue
                 WHERE tenant_id = $1::uuid AND envelope_id = $2::uuid
                """,
                tenant_id, envelope_id,
            )
    return _row_to_dict(row, now) if row else None
