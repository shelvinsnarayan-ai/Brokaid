"""Phase R5b-2a — claims scene kernel (dispatch-aware).

Extract-first (byte-verbatim) from `routes/c_claims.py`. The route
still owns the Brain call + the MOCKED insurer_lodgement adapter +
composition; this kernel owns:

  * client-lookup with brokerage display (dispatch-aware read)
  * next-claim-reference sequence (dispatch-aware read)
  * the 6 FNOL event emitters (byte-verbatim payload/actor/idem
    from the pre-extraction c_claims.submit_fnol)
  * claims row INSERT + status update (dispatch)
  * proj_claim_status_updates INSERT (dispatch)
  * recipient email lookup (dispatch)

The 6 events, in order:
  1. ClientRequestReceived (human client_owner)
  2. ClaimTriaged (system claims)
  3. ClaimCreated (system claims, mocked=False)
  4. InsurerLodgementRequested (system insurer-channel, mocked=True)
  5. InsurerLodgementAck (system insurer-channel, mocked=True)
  6. QueueCardEnqueued (system queue, optional — urgent or coverage)

Byte-identical event_hash across backends for identical inputs (R2).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.tenant_safe import TenantSafeDatabase, wrap as _wrap_safe
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch


def _is_mongo(store) -> bool:
    return isinstance(store, (AsyncIOMotorDatabase, TenantSafeDatabase))


async def _emit(store, tenant_id: str, **kw):
    if _is_mongo(store):
        async with use_tenant(tenant_id):
            return await append_via_dispatch(store, tenant_id, **kw)
    return await append_via_dispatch(store, tenant_id, **kw)


# ---- Reads -----------------------------------------------------------
async def load_client_for_user(
    store, tenant_id: str, user_id: str,
) -> dict[str, Any] | None:
    """Return client + brokerage_display for a client-owner session.
    Byte-verbatim shape from pre-extraction `_load_client`."""
    if _is_mongo(store):
        safe = _wrap_safe(store)
        async with use_tenant(tenant_id):
            from app.kernel.tenant_ctx import ACTIVE_CLIENT
            active = ACTIVE_CLIENT.get()
            link = (active and await safe.user_client_links.find_one({"user_id": user_id, "client_id": active})) \
                or await safe.user_client_links.find_one({"user_id": user_id})
            if not link:
                return None
            client = await safe.clients.find_one(
                {"client_id": link["client_id"]}
            )
            if not client:
                return None
            brok = await safe.brokerages.find_one({})
        return {
            "client_id": client["client_id"],
            "display_name": client.get("display_name"),
            "brokerage_display": (brok or {}).get("display_name"),
        }
    async with store.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id,
            )
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
                user_id, tenant_id,
            )
    return dict(row) if row else None


async def next_claim_reference(store, tenant_id: str) -> str:
    """CLM-YYYY-NNNNNN, monotonic per tenant per year. Byte-verbatim
    from pre-extraction `_next_reference`."""
    year = datetime.now(timezone.utc).year
    if _is_mongo(store):
        safe = _wrap_safe(store)
        max_n = 0
        prefix = f"CLM-{year}-"
        async with use_tenant(tenant_id):
            async for r in safe.claims.find({}):
                ref = r.get("claim_reference") or ""
                seq = ref[len(prefix):]
                if ref.startswith(prefix) and len(seq) == 6 and seq.isdigit():  # R1.8: the year digits no longer leak into the sequence
                    max_n = max(max_n, int(seq))
        return f"{prefix}{max_n + 1:06d}"
    async with store.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id,
            )
            row = await c.fetchrow(
                """
                SELECT COALESCE(
                  MAX(NULLIF(regexp_replace(claim_reference, '\\D', '', 'g'),
                             '')::bigint), 0
                ) AS n
                  FROM claims
                 WHERE tenant_id=$1::uuid
                   AND claim_reference LIKE $2
                """,
                tenant_id, f"CLM-{year}-%",
            )
    n = int(row["n"] or 0) + 1
    return f"CLM-{year}-{n:06d}"


async def load_recipient_email(
    store, tenant_id: str, client_id: str,
) -> str | None:
    """First linked user's email for the client (best-effort notify addr)."""
    if _is_mongo(store):
        safe = _wrap_safe(store)
        async with use_tenant(tenant_id):
            link = await safe.user_client_links.find_one({"client_id": client_id})
            if not link:
                return None
            u = await safe.users.find_one({"user_id": link["user_id"]})
        return (u or {}).get("email")
    async with store.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id,
            )
            row = await c.fetchrow(
                "SELECT u.email FROM users u "
                "JOIN user_client_links l ON l.user_id = u.user_id "
                "WHERE l.client_id=$1::uuid AND l.tenant_id=$2::uuid "
                "LIMIT 1",
                client_id, tenant_id,
            )
    return row["email"] if row else None


# ---- Row writes ------------------------------------------------------
async def insert_claim_row(
    store, tenant_id: str, *,
    claim_id: str, client_id: str, policy_id: str | None,
    claim_reference: str, incident_date: date | None,
    description: str, claim_type: str | None, urgency: str | None,
    opened_by_event: str,
) -> None:
    if _is_mongo(store):
        safe = _wrap_safe(store)
        # BSON cannot encode `datetime.date`; convert to a
        # UTC-midnight datetime so the Mongo document round-trips.
        incident_dt = (
            datetime(
                incident_date.year, incident_date.month, incident_date.day,
                tzinfo=timezone.utc,
            )
            if isinstance(incident_date, date) and not isinstance(incident_date, datetime)
            else incident_date
        )
        async with use_tenant(tenant_id):
            await safe.claims.insert_one({
                "claim_id": claim_id,
                "client_id": client_id,
                "policy_id": policy_id,
                "claim_reference": claim_reference,
                "incident_date": incident_dt,
                "description": description,
                "claim_type": claim_type,
                "urgency": urgency,
                "status": "triaged",
                "opened_by_event": opened_by_event,
            })
        return
    async with store.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id,
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
                claim_id, tenant_id, client_id,
                (policy_id or None), claim_reference,
                incident_date, description,
                claim_type, urgency, opened_by_event,
            )


async def mark_claim_lodged(store, tenant_id: str, claim_id: str) -> None:
    if _is_mongo(store):
        safe = _wrap_safe(store)
        async with use_tenant(tenant_id):
            await safe.claims.update_one(
                {"claim_id": claim_id},
                {"$set": {"status": "lodged"}},
            )
        return
    async with store.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id,
            )
            await c.execute(
                "UPDATE claims SET status='lodged' WHERE claim_id=$1::uuid",
                claim_id,
            )


async def append_status_update(
    store, tenant_id: str, *,
    claim_id: str, event_id: str, kind: str,
    summary: str, mocked: bool,
) -> None:
    if _is_mongo(store):
        safe = _wrap_safe(store)
        async with use_tenant(tenant_id):
            await safe.proj_claim_status_updates.insert_one({
                "claim_id": claim_id,
                "event_id": event_id,
                "ts": datetime.now(timezone.utc),
                "kind": kind,
                "summary": summary,
                "mocked": mocked,
            })
        return
    async with store.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id,
            )
            await c.execute(
                """
                INSERT INTO proj_claim_status_updates
                  (claim_id, tenant_id, event_id, ts, kind, summary, mocked)
                VALUES ($1::uuid, $2::uuid, $3::uuid, NOW(),
                        $4, $5, $6)
                """,
                claim_id, tenant_id, event_id, kind, summary, mocked,
            )


# ---- 6 event emitters — byte-verbatim from pre-extraction ------------
async def emit_client_request_received(
    store, *, tenant_id: str, user_id: str, envelope_id: str,
    client_id: str, raw_message: str,
    policy_id: str | None, incident_date: str | None,
) -> dict[str, Any]:
    return await _emit(
        store, tenant_id, type="ClientRequestReceived",
        actor={"kind": "human", "id": user_id, "name": "client_owner"},
        payload={
            "envelope_id": envelope_id,
            "matter_kind": "claim",
            "client_id": client_id,
            "raw_message": raw_message,
            "policy_id": policy_id,
            "incident_date": incident_date,
        },
        idempotency_key=f"claim:req:{envelope_id}",
    )


async def emit_claim_triaged(
    store, *, tenant_id: str, envelope_id: str, client_id: str,
    triage: dict[str, Any],
) -> dict[str, Any]:
    return await _emit(
        store, tenant_id, type="ClaimTriaged",
        actor={"kind": "system", "id": "claims", "name": "claims"},
        payload={
            "envelope_id": envelope_id,
            "client_id": client_id,
            "claim_type": triage.get("claim_type"),
            "urgency": triage.get("urgency"),
            "key_facts": triage.get("key_facts") or [],
            "requires_coverage_check": bool(triage.get("requires_coverage_check")),
            "structured_summary": triage.get("structured_summary"),
        },
        idempotency_key=f"claim:triaged:{envelope_id}",
    )


async def emit_claim_created(
    store, *, tenant_id: str, envelope_id: str, claim_id: str,
    claim_reference: str, client_id: str, policy_id: str | None,
    claim_type: str | None, urgency: str | None,
    description: str, incident_date: str | None,
) -> dict[str, Any]:
    return await _emit(
        store, tenant_id, type="ClaimCreated",
        actor={"kind": "system", "id": "claims", "name": "claims"},
        payload={
            "envelope_id": envelope_id,
            "claim_id": claim_id,
            "claim_reference": claim_reference,
            "client_id": client_id,
            "policy_id": policy_id,
            "claim_type": claim_type,
            "urgency": urgency,
            "description": description,
            "incident_date": incident_date,
            "status": "triaged",
        },
        idempotency_key=f"claim:created:{claim_id}",
    )


async def emit_insurer_lodgement_requested(
    store, *, tenant_id: str, envelope_id: str,
    claim_id: str, claim_reference: str,
) -> dict[str, Any]:
    return await _emit(
        store, tenant_id, type="InsurerLodgementRequested",
        actor={"kind": "system", "id": "insurer-channel", "name": "insurer"},
        payload={
            "envelope_id": envelope_id,
            "claim_id": claim_id,
            "claim_reference": claim_reference,
            "mocked": True,
        },
        mocked=True,
        idempotency_key=f"lodgement:req:{claim_id}",
    )


async def emit_insurer_lodgement_ack(
    store, *, tenant_id: str, envelope_id: str,
    claim_id: str, claim_reference: str, lodgement: dict[str, Any],
) -> dict[str, Any]:
    return await _emit(
        store, tenant_id, type="InsurerLodgementAck",
        actor={"kind": "system", "id": "insurer-channel", "name": "insurer"},
        payload={
            "envelope_id": envelope_id,
            "claim_id": claim_id,
            "claim_reference": claim_reference,
            "insurer_display": lodgement["insurer_display"],
            "insurer_reference": lodgement["insurer_reference"],
            "lodgement_id": lodgement["lodgement_id"],
            "mocked": True,
        },
        mocked=True,
        idempotency_key=f"lodgement:ack:{claim_id}",
    )


async def emit_queue_card_enqueued(
    store, *, tenant_id: str, envelope_id: str,
    claim_id: str, class_key: str = "claim_admin",
) -> dict[str, Any]:
    return await _emit(
        store, tenant_id, type="QueueCardEnqueued",
        actor={"kind": "system", "id": "queue", "name": "queue"},
        payload={
            "envelope_id": envelope_id,
            "claim_id": claim_id,
            "class_key": class_key,
        },
        idempotency_key=f"claim:queued:{envelope_id}",
    )


# ---- R5b-2b: additional helpers for c_ask + o_claims route rewires ---
async def load_brokerage_display(store, tenant_id: str) -> str | None:
    """First brokerage's display_name for the tenant. Byte-verbatim
    from pre-extraction `o_claims.status_update` + `c_ask.
    _load_client_and_broker` brokerage lookups."""
    if _is_mongo(store):
        safe = _wrap_safe(store)
        async with use_tenant(tenant_id):
            row = await safe.brokerages.find_one({})
        return (row or {}).get("display_name")
    async with store.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id,
            )
            row = await c.fetchrow(
                "SELECT display_name FROM brokerages "
                "WHERE tenant_id=$1::uuid LIMIT 1", tenant_id,
            )
    return row["display_name"] if row else None


async def load_claim_for_operator(
    store, tenant_id: str, claim_id: str,
) -> dict[str, Any] | None:
    """Byte-verbatim from `o_claims._load_claim`. Returns
    `{claim_id, client_id, claim_reference, status, policy_id}`."""
    if _is_mongo(store):
        safe = _wrap_safe(store)
        async with use_tenant(tenant_id):
            row = await safe.claims.find_one({"claim_id": claim_id})
        if not row:
            return None
        return {
            "claim_id": row["claim_id"],
            "client_id": row["client_id"],
            "claim_reference": row.get("claim_reference"),
            "status": row.get("status"),
            "policy_id": row.get("policy_id"),
        }
    async with store.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id,
            )
            row = await c.fetchrow(
                """
                SELECT claim_id::text, client_id::text, claim_reference, status,
                       policy_id::text
                  FROM claims
                 WHERE tenant_id=$1::uuid AND claim_id=$2::uuid
                """,
                tenant_id, claim_id,
            )
    return dict(row) if row else None


async def mark_claim_resolved(store, tenant_id: str, claim_id: str) -> None:
    """UPDATE claims SET status='resolved', resolved_at=NOW(). PG
    branch is byte-verbatim from pre-extraction `o_claims.
    resolve_claim`; Mongo branch mirrors the semantics with
    `datetime.now(timezone.utc)`."""
    if _is_mongo(store):
        safe = _wrap_safe(store)
        async with use_tenant(tenant_id):
            await safe.claims.update_one(
                {"claim_id": claim_id},
                {"$set": {
                    "status": "resolved",
                    "resolved_at": datetime.now(timezone.utc),
                }},
            )
        return
    async with store.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id,
            )
            await c.execute(
                "UPDATE claims SET status='resolved', resolved_at=NOW() "
                " WHERE tenant_id=$1::uuid AND claim_id=$2::uuid",
                tenant_id, claim_id,
            )


async def emit_claim_resolved(
    store, *, tenant_id: str, user_id: str, actor_name: str,
    claim_id: str, claim_reference: str | None,
) -> dict[str, Any]:
    """Byte-verbatim from pre-extraction `o_claims.resolve_claim`
    trail_append call."""
    return await _emit(
        store, tenant_id, type="ClaimResolved",
        actor={"kind": "human", "id": user_id, "name": actor_name},
        payload={
            "claim_id": claim_id,
            "claim_reference": claim_reference,
            "status": "resolved",
        },
        idempotency_key=f"claim:resolved:{claim_id}",
    )


async def emit_client_question_asked(
    store, *, tenant_id: str, user_id: str, envelope_id: str,
    client_id: str, message: str,
) -> dict[str, Any]:
    """Byte-verbatim from pre-extraction `c_ask.send_message` first
    trail append."""
    return await _emit(
        store, tenant_id, type="ClientQuestionAsked",
        actor={"kind": "human", "id": user_id, "name": "client_owner"},
        payload={
            "envelope_id": envelope_id,
            "client_id": client_id,
            "message": message,
        },
        idempotency_key=f"ask:in:{envelope_id}",
    )


async def recent_client_events_summary(
    store, tenant_id: str, client_id: str,
) -> str:
    """Last 8 events for a client, joined as 'Recent: T1, T2, ...'.
    Byte-verbatim from pre-extraction `c_ask._recent_events_summary`
    with Mongo branch added."""
    if _is_mongo(store):
        safe = _wrap_safe(store)
        types: list[str] = []
        async with use_tenant(tenant_id):
            cursor = safe.events.find(
                {"payload.client_id": client_id},
            ).sort("seq", -1).limit(8)
            async for r in cursor:
                types.append(r["type"])
        if not types:
            return "(no recent events)"
        return "Recent: " + ", ".join(types)
    async with store.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id,
            )
            rows = await c.fetch(
                """
                SELECT type, payload FROM events
                 WHERE tenant_id=$1::uuid
                   AND payload->>'client_id' = $2
                 ORDER BY seq DESC LIMIT 8
                """,
                tenant_id, client_id,
            )
    if not rows:
        return "(no recent events)"
    parts = []
    for r in rows:
        parts.append(r["type"])
    return "Recent: " + ", ".join(parts)
