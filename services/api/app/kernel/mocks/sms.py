"""MOCKED SMS adapter.

Every SMS goes through the phase-10 release pipeline first. If the
client has `vulnerable_flag=true`, the send is suppressed and an
operator queue card (`comms_service`, gate_reason=`vulnerable_flag_set`)
is enqueued instead.

Test counter `SENT_SMS_COUNT` — the number of times the mock channel
was actually invoked. Suppressed sends do NOT increment it. Tests
inspect this to assert the vulnerable-flag beat kept its finger off
the trigger.
"""
from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import asyncpg

from app.kernel.queue import enqueue as queue_enqueue
from app.kernel.release import try_release
from app.kernel.trail import append


# Test-visible counter — tests reset via reset_counters().
SENT_SMS_COUNT: int = 0


def reset_counters() -> None:
    global SENT_SMS_COUNT
    SENT_SMS_COUNT = 0


def _det_uuid(seed: str) -> str:
    return str(uuid.UUID(bytes=hashlib.sha256(seed.encode()).digest()[:16]))


async def _lookup_vulnerable_flag(
    pool: asyncpg.Pool, tenant_id: str, client_id: str,
) -> tuple[bool, str]:
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            row = await c.fetchrow(
                "SELECT vulnerable_flag, display_name FROM clients "
                "WHERE tenant_id=$1::uuid AND client_id=$2::uuid",
                tenant_id, client_id,
            )
    if row is None:
        return False, ""
    return bool(row["vulnerable_flag"]), row["display_name"]


async def send(
    *,
    pool: asyncpg.Pool,
    tenant_id: str,
    envelope_id: str,
    client_id: str,
    matter_id: str | None,
    message: str,
    context: dict[str, Any] | None = None,
    author_agent: str = "notify",
) -> dict[str, Any]:
    """Send an SMS through the release pipeline + vulnerable-flag check.

    Returns:
      {status: 'sent' | 'suppressed_for_operator_review' | 'blocked_or_rewrite',
       sms_id?: str, mocked: true}
    """
    global SENT_SMS_COUNT

    vuln, _client_name = await _lookup_vulnerable_flag(pool, tenant_id, client_id)

    if vuln:
        # 1. Emit the suppression event first — it precedes queue enqueue
        #    so the audit trail is: SmsSuppressedForOperatorReview →
        #    QueueCardEnqueued.
        sup = await append(
            pool, tenant_id, "SmsSuppressedForOperatorReview",
            actor={"kind": "system", "id": "sms", "name": "sms"},
            payload={
                "envelope_id": envelope_id,
                "client_id": client_id,
                "matter_id": matter_id,
                "message": message,
                "reason": "vulnerable_flag",
                "would_have_sent": True,
                "mocked": True,
            },
            mocked=True,
            idempotency_key=f"sms:suppressed:{envelope_id}",
        )
        # 2. Enqueue on operator queue as comms_service HITL.
        await queue_enqueue(
            pool,
            tenant_id=tenant_id,
            envelope_id=envelope_id,
            class_key="comms_service",
            proposal_ref=sup.event_id,
            matter_id=matter_id,
            client_id=client_id,
            gate_reason="vulnerable_flag_set",
            confidence=None,
            routed_to_role="account_executive",
        )
        return {
            "status": "suppressed_for_operator_review",
            "reason": "vulnerable_flag",
            "mocked": True,
        }

    # Non-vulnerable path: run through release pipeline first.
    release = await try_release(
        pool=pool,
        tenant_id=tenant_id,
        draft=message,
        context=context or {"matter_kind": "general"},
        author_agent=author_agent,
        matter_id=matter_id,
        envelope_id=envelope_id,
        client_id=client_id,
        mocked_channels_used=["sms"],
    )
    if not release.released:
        # Release pipeline blocked or asked for rewrite / HITL. Do NOT send.
        return {
            "status": "blocked_or_rewrite",
            "release_verdict": release.verdict,
            "mocked": True,
        }

    # Fire the mocked channel.
    await asyncio.sleep(0.05)
    SENT_SMS_COUNT += 1
    sms_id = _det_uuid(f"sms:{envelope_id}")
    await append(
        pool, tenant_id, "SmsQueued",
        actor={"kind": "system", "id": "sms", "name": "sms"},
        payload={
            "envelope_id": envelope_id,
            "client_id": client_id,
            "matter_id": matter_id,
            "sms_id": sms_id,
            "message": message,
            "queued_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            "would_have_sent": True,
            "mocked": True,
        },
        mocked=True,
        idempotency_key=f"sms:queued:{envelope_id}",
    )
    return {"status": "sent", "sms_id": sms_id, "mocked": True}
