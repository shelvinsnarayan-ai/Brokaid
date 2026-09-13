"""Endorsement execution — runs after operator approval + mocked
payment + mocked e-sign, in that lineage.

Emits `EndorsementExecuted` and `PolicyUpdated`, then updates the
policies.schedule projection in the same transaction as the event
write.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import asyncpg

from app.kernel.trail import append


class EndorsementLineageIncomplete(RuntimeError):
    """Fired if the required upstream events aren't all present."""


async def _load_lineage(
    pool: asyncpg.Pool, tenant_id: str, envelope_id: str,
) -> dict[str, Any]:
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            rows = await c.fetch(
                """
                SELECT type, payload FROM events
                 WHERE tenant_id=$1::uuid
                   AND payload->>'envelope_id' = $2
                 ORDER BY seq ASC
                """,
                tenant_id, envelope_id,
            )
    lineage: dict[str, Any] = {}
    for r in rows:
        p = json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"]
        lineage.setdefault(r["type"], p)   # first occurrence
    return lineage


def _apply_change(schedule: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    """Naïve dot-path setter used by the mocked scene. Applies a
    `field_path` + `to_value` write into the policy schedule.

    intAIke / plAIce emit paths of the form `schedule.limits.public_liability`.
    The leading `schedule.` prefix is stripped because the caller has already
    handed us the schedule dict itself, not a wrapping envelope.
    """
    raw = change.get("field_path") or ""
    if raw.startswith("schedule."):
        raw = raw[len("schedule."):]
    path = raw.split(".") if raw else []
    op = change.get("operation") or "set"
    if not path or path == [""]:
        return schedule
    cur = schedule
    for p in path[:-1]:
        cur = cur.setdefault(p, {})
        if not isinstance(cur, dict):
            return schedule
    last = path[-1]
    if op == "set" or op == "increase" or op == "decrease":
        cur[last] = change.get("to_value")
    elif op == "add":
        arr = cur.setdefault(last, [])
        if isinstance(arr, list):
            arr.append(change.get("to_value"))
    elif op == "remove":
        cur.pop(last, None)
    return schedule


async def execute_endorsement(
    *,
    pool: asyncpg.Pool,
    tenant_id: str,
    envelope_id: str,
    policy_id: str,
    approver_actor: dict[str, Any],
) -> dict[str, Any]:
    """Run after Approval + PaymentReceived + SignatureCompleted.

    Emits `EndorsementExecuted` + `PolicyUpdated`, updates the
    policies row.  Returns the endorsement payload dict.
    """
    lineage = await _load_lineage(pool, tenant_id, envelope_id)
    for required in ("Approval", "PaymentReceived", "SignatureCompleted"):
        if required not in lineage:
            raise EndorsementLineageIncomplete(
                f"missing upstream event: {required}"
            )

    # Determine the changes to apply from the placement submission.
    submission = lineage.get("SubmissionDrafted") or {}
    structured = submission.get("structured_submission") or {}
    requested_changes = (
        structured.get("requested_changes")
        or submission.get("requested_changes")
        or []
    )

    endorsement_id = str(uuid.uuid4())
    effective_date = datetime.now(timezone.utc).isoformat(timespec="microseconds")

    # Read current schedule + apply changes + write back inside a txn.
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            row = await c.fetchrow(
                "SELECT schedule FROM policies "
                "WHERE tenant_id=$1::uuid AND policy_id=$2::uuid",
                tenant_id, policy_id,
            )
            if row is None:
                raise EndorsementLineageIncomplete(
                    f"policy {policy_id} not found"
                )
            schedule = (
                json.loads(row["schedule"])
                if isinstance(row["schedule"], str)
                else dict(row["schedule"])
            )
            for ch in requested_changes:
                _apply_change(schedule, ch)
            await c.execute(
                "UPDATE policies SET schedule=$3::jsonb "
                "WHERE tenant_id=$1::uuid AND policy_id=$2::uuid",
                tenant_id, policy_id, json.dumps(schedule),
            )

    await append(
        pool, tenant_id, "EndorsementExecuted",
        actor=approver_actor,
        payload={
            "envelope_id": envelope_id,
            "endorsement_id": endorsement_id,
            "policy_id": policy_id,
            "changes_applied": requested_changes,
            "effective_date": effective_date,
            "mocked_channels": ["carrier", "payment", "e_sign"],
        },
        idempotency_key=f"endorsement:exec:{envelope_id}",
    )
    await append(
        pool, tenant_id, "PolicyUpdated",
        actor={"kind": "system", "id": "endorsement", "name": "endorsement"},
        payload={
            "envelope_id": envelope_id,
            "policy_id": policy_id,
            "schedule_after": schedule,
            "endorsement_id": endorsement_id,
        },
        idempotency_key=f"policy:updated:{envelope_id}",
    )
    return {
        "endorsement_id": endorsement_id,
        "policy_id": policy_id,
        "changes_applied": requested_changes,
        "effective_date": effective_date,
        "schedule_after": schedule,
    }
