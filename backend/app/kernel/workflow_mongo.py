"""Phase R4b — Mongo backend for the workflow layer.

Covers the collections in the `/api/o/queue` flow's write / read
paths:
  envelopes            — the workflow object; carries the ledger
                         event proposal_ref + client/policy context.
  proj_queue           — HITL queue projection; the operator surface.
  proj_active_blocks   — compliance blocks; blocks queue drain.

Design mirrors R4a: everything flows through `TenantSafeDatabase`.
No new `_SYSTEM_ALLOW` entries — all three are tenant-scoped.
The R2 events collection is untouched; queue enqueue calls
`trail_mongo.append_event` for `QueueCardEnqueued` as the Postgres
side calls `trail.append`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

from app.db.tenant_safe import TenantSafeDatabase, wrap
from app.kernel.tenant_ctx import get_tenant_id


# ---- Index bootstrap (idempotent) ------------------------------------
_indexes_initialised: set[int] = set()


async def _ensure_indexes(safe_db: TenantSafeDatabase) -> None:
    raw = safe_db.raw
    key = id(raw)
    if key in _indexes_initialised:
        return
    await raw["envelopes"].create_index(
        [("tenant_id", ASCENDING), ("envelope_id", ASCENDING)],
        unique=True, name="env_tenant_id_uniq",
    )
    await raw["envelopes"].create_index(
        [("tenant_id", ASCENDING), ("client_id", ASCENDING)],
        name="env_tenant_client",
    )
    await raw["proj_queue"].create_index(
        [("tenant_id", ASCENDING), ("envelope_id", ASCENDING)],
        unique=True, name="pq_tenant_env_uniq",
    )
    await raw["proj_queue"].create_index(
        [("tenant_id", ASCENDING), ("resolved_at", ASCENDING), ("sla_deadline", ASCENDING)],
        name="pq_tenant_pending",
    )
    await raw["proj_active_blocks"].create_index(
        [("tenant_id", ASCENDING), ("block_id", ASCENDING)],
        unique=True, name="pab_tenant_block_uniq",
    )
    _indexes_initialised.add(key)


# ---- Envelopes -------------------------------------------------------
@dataclass(frozen=True)
class Envelope:
    envelope_id: str
    tenant_id: str
    kind: str                 # intake | placement | endorsement | claim | ...
    client_id: Optional[str]
    policy_id: Optional[str]
    status: str
    created_at: datetime


async def create_envelope(
    db, *, envelope_id: str, kind: str, status: str = "open",
    client_id: Optional[str] = None, policy_id: Optional[str] = None,
) -> Envelope:
    safe_db = wrap(db)
    await _ensure_indexes(safe_db)
    tenant_id = get_tenant_id()
    doc = {
        "envelope_id": envelope_id,
        "tenant_id": tenant_id,
        "kind": kind,
        "client_id": client_id,
        "policy_id": policy_id,
        "status": status,
        "created_at": datetime.now(timezone.utc),
    }
    try:
        await safe_db.envelopes.insert_one(doc)
    except DuplicateKeyError:
        # Idempotent — return existing.
        pass
    return await find_envelope(db, envelope_id)


async def find_envelope(db, envelope_id: str) -> Optional[Envelope]:
    safe_db = wrap(db)
    await _ensure_indexes(safe_db)
    row = await safe_db.envelopes.find_one({"envelope_id": envelope_id})
    if row is None:
        return None
    return Envelope(
        envelope_id=row["envelope_id"], tenant_id=row["tenant_id"],
        kind=row["kind"], client_id=row.get("client_id"),
        policy_id=row.get("policy_id"), status=row["status"],
        created_at=row["created_at"],
    )


async def set_envelope_status(db, envelope_id: str, status: str) -> None:
    safe_db = wrap(db)
    await safe_db.envelopes.update_one(
        {"envelope_id": envelope_id},
        {"$set": {"status": status, "updated_at": datetime.now(timezone.utc)}},
    )


# ---- Queue -----------------------------------------------------------
@dataclass(frozen=True)
class QueueCard:
    envelope_id: str
    class_key: str
    matter_id: Optional[str]
    client_id: Optional[str]
    proposal_ref: str
    hitl_since: datetime
    sla_deadline: Optional[datetime]
    gate_reason: Optional[str]
    confidence: Optional[float]
    routed_to_role: Optional[str]
    resolved_at: Optional[datetime]
    resolved_by_event: Optional[str]


async def enqueue(
    db, *, envelope_id: str, class_key: str, proposal_ref: str,
    matter_id: Optional[str] = None, client_id: Optional[str] = None,
    gate_reason: Optional[str] = None, confidence: Optional[float] = None,
    routed_to_role: Optional[str] = None,
    hitl_since: Optional[datetime] = None,
    sla_deadline: Optional[datetime] = None,
) -> QueueCard:
    """Idempotent enqueue on Mongo. Does NOT emit `QueueCardEnqueued`
    — that's the caller's responsibility via `trail_mongo.append_event`
    (mirrors the Postgres path in `kernel.queue.enqueue`)."""
    from app.kernel.queue import sla_deadline_for
    safe_db = wrap(db)
    await _ensure_indexes(safe_db)
    tenant_id = get_tenant_id()
    now = hitl_since or datetime.now(timezone.utc)
    deadline = sla_deadline or sla_deadline_for(class_key, now)
    doc = {
        "tenant_id": tenant_id,
        "envelope_id": envelope_id,
        "matter_id": matter_id,
        "client_id": client_id,
        "class_key": class_key,
        "proposal_ref": proposal_ref,
        "hitl_since": now,
        "sla_deadline": deadline,
        "gate_reason": gate_reason,
        "confidence": confidence,
        "routed_to_role": routed_to_role,
        "resolved_at": None,
        "resolved_by_event": None,
    }
    try:
        await safe_db.proj_queue.insert_one(doc)
    except DuplicateKeyError:
        pass
    return QueueCard(
        envelope_id=envelope_id, class_key=class_key,
        matter_id=matter_id, client_id=client_id,
        proposal_ref=proposal_ref, hitl_since=now,
        sla_deadline=deadline, gate_reason=gate_reason,
        confidence=confidence, routed_to_role=routed_to_role,
        resolved_at=None, resolved_by_event=None,
    )


async def resolve_queue_card(
    db, envelope_id: str, resolved_by_event: str,
) -> None:
    safe_db = wrap(db)
    await safe_db.proj_queue.update_one(
        {"envelope_id": envelope_id, "resolved_at": None},
        {"$set": {
            "resolved_at": datetime.now(timezone.utc),
            "resolved_by_event": resolved_by_event,
        }},
    )


async def fetch_pending_queue(db) -> list[dict[str, Any]]:
    safe_db = wrap(db)
    await _ensure_indexes(safe_db)
    now = datetime.now(timezone.utc)
    out = []
    tids = [c["client_id"] async for c in safe_db.clients.find({"test": True}, {"client_id": 1})]
    cursor = safe_db.proj_queue.find({"resolved_at": None, "client_id": {"$nin": tids}}).sort(
        [("sla_deadline", ASCENDING), ("hitl_since", ASCENDING)]
    )
    async for r in cursor:
        deadline = r.get("sla_deadline")
        state = "ok"
        if deadline is not None:
            deadline_aware = deadline if deadline.tzinfo else deadline.replace(tzinfo=timezone.utc)
            if now >= deadline_aware:
                state = "overdue"
            elif (deadline_aware - now).total_seconds() <= 900:
                state = "warn"
        out.append({
            "envelope_id": r["envelope_id"], "class_key": r["class_key"],
            "matter_id": r.get("matter_id"), "client_id": r.get("client_id"),
            "proposal_ref": r["proposal_ref"],
            "hitl_since": r["hitl_since"].isoformat() if r["hitl_since"] else None,
            "sla_deadline": deadline.isoformat() if deadline else None,
            "sla_state": state,
            "gate_reason": r.get("gate_reason"),
            "confidence": r.get("confidence"),
            "routed_to_role": r.get("routed_to_role"),
        })
    return out


async def fetch_queue_card(db, envelope_id: str) -> Optional[dict[str, Any]]:
    safe_db = wrap(db)
    await _ensure_indexes(safe_db)
    r = await safe_db.proj_queue.find_one({"envelope_id": envelope_id})
    if r is None:
        return None
    return {
        "envelope_id": r["envelope_id"], "class_key": r["class_key"],
        "matter_id": r.get("matter_id"), "client_id": r.get("client_id"),
        "proposal_ref": r["proposal_ref"],
        "hitl_since": r["hitl_since"].isoformat() if r["hitl_since"] else None,
        "sla_deadline": r["sla_deadline"].isoformat() if r.get("sla_deadline") else None,
        "gate_reason": r.get("gate_reason"),
        "confidence": r.get("confidence"),
        "routed_to_role": r.get("routed_to_role"),
        "resolved_at": r["resolved_at"].isoformat() if r.get("resolved_at") else None,
        "resolved_by_event": r.get("resolved_by_event"),
    }


# ---- Active blocks ---------------------------------------------------
@dataclass(frozen=True)
class ActiveBlock:
    block_id: str
    tenant_id: str
    target_kind: str
    target_id: str
    reason: str
    added_by_event: str
    cleared_at: Optional[datetime]
    cleared_by_event: Optional[str]


async def add_active_block(
    db, *, block_id: str, target_kind: str, target_id: str,
    reason: str, added_by_event: str,
) -> None:
    safe_db = wrap(db)
    await _ensure_indexes(safe_db)
    try:
        await safe_db.proj_active_blocks.insert_one({
            "tenant_id": get_tenant_id(),
            "block_id": block_id, "target_kind": target_kind,
            "target_id": target_id, "reason": reason,
            "added_by_event": added_by_event,
            "cleared_at": None, "cleared_by_event": None,
        })
    except DuplicateKeyError:
        pass


async def clear_active_block(
    db, block_id: str, cleared_reason: str, cleared_by_event: str,
) -> None:
    safe_db = wrap(db)
    await safe_db.proj_active_blocks.update_one(
        {"block_id": block_id, "cleared_at": None},
        {"$set": {
            "cleared_at": datetime.now(timezone.utc),
            "cleared_reason": cleared_reason,
            "cleared_by_event": cleared_by_event,
        }},
    )


async def list_active_blocks(db) -> list[dict[str, Any]]:
    safe_db = wrap(db)
    await _ensure_indexes(safe_db)
    out = []
    async for r in safe_db.proj_active_blocks.find({"cleared_at": None}):
        out.append({
            "block_id": r["block_id"], "target_kind": r["target_kind"],
            "target_id": r["target_id"], "reason": r["reason"],
            "added_by_event": r["added_by_event"],
        })
    return out


# ---- In-line projection dispatch for the events R2 emits -------------
# Mirrors trail.py::_project for the workflow-layer projections. Called
# from `trail_mongo.append_event`'s projection hook in R5; today R4b
# just makes the projection ops directly callable so the parity + E2E
# tests can exercise them.
async def project_event(db, ev) -> None:
    """Apply a workflow-layer projection for a single event.

    R4b: ComplianceBlockAdded / ComplianceBlockCleared.
    R4c extension: the six remaining event types from trail.py::_project
    (MatterCreated, MatterStatusChanged, WorkflowWait, WorkflowResumed,
    TimerFired, AgentOutputProduced, SprintCapAlert). Byte-for-byte
    mirror of the Postgres path — same event → same projection write.
    """
    from datetime import datetime, timezone

    t = getattr(ev, "type", None) or ev.get("type")
    p = getattr(ev, "payload", None) or ev.get("payload", {})
    event_id = getattr(ev, "event_id", None) or ev.get("event_id")
    ts = getattr(ev, "ts", None) or ev.get("ts")

    def _parse_ts(v):
        if v is None:
            return None
        if isinstance(v, str):
            try:
                return datetime.fromisoformat(v)
            except ValueError:
                return None
        return v

    safe_db = wrap(db)

    if t == "ComplianceBlockAdded":
        await add_active_block(
            db,
            block_id=p["block_id"], target_kind=p["target_kind"],
            target_id=p["target_id"], reason=p.get("reason", ""),
            added_by_event=event_id,
        )
    elif t == "ComplianceBlockCleared":
        await clear_active_block(
            db, p["block_id"], p.get("reason", ""), event_id,
        )
    elif t == "MatterCreated":
        await safe_db.proj_matters.update_one(
            {"matter_id": p["matter_id"]},
            {"$set": {
                "matter_id": p["matter_id"],
                "client_id": p["client_id"],
                "status": p["status"], "subject": p["subject"],
                "updated_at": _parse_ts(ts) or datetime.now(timezone.utc),
                "updated_by_event": event_id,
            }},
            upsert=True,
        )
    elif t == "MatterStatusChanged":
        await safe_db.proj_matters.update_one(
            {"matter_id": p["matter_id"]},
            {"$set": {
                "status": p["status"],
                "updated_at": _parse_ts(ts) or datetime.now(timezone.utc),
                "updated_by_event": event_id,
            }},
        )
    elif t == "WorkflowWait":
        await safe_db.proj_workflow_waits.update_one(
            {"workflow_id": p["workflow_id"], "step": p["step"]},
            {"$set": {
                "workflow_id": p["workflow_id"], "step": p["step"],
                "resume_predicate": p.get("resume_predicate", {}),
                "fire_at": _parse_ts(p.get("fire_at")),
                "timeout_at": _parse_ts(p.get("timeout_at")),
                "resolved": False,
                "updated_by_event": event_id,
            }},
            upsert=True,
        )
    elif t in ("WorkflowResumed", "TimerFired"):
        await safe_db.proj_workflow_waits.update_one(
            {"workflow_id": p["workflow_id"], "step": p["step"]},
            {"$set": {
                "resolved": True,
                "resolved_at": _parse_ts(ts) or datetime.now(timezone.utc),
                "updated_by_event": event_id,
            }},
        )
    elif t == "AgentOutputProduced":
        tokens = p.get("tokens_used") or {}
        input_tok = int(tokens.get("input", 0))
        output_tok = int(tokens.get("output", 0))
        delta = input_tok + output_tok
        # Upsert proj_sprint_usage.
        await safe_db.proj_sprint_usage.update_one(
            {},
            {"$inc": {"tokens_used": delta, "invocations": 1,
                      "cost_usd_total": float(p.get("cost_usd") or 0.0)},
             "$set": {"updated_by_event": event_id},
             "$setOnInsert": {"last_alert_pct": 0}},
            upsert=True,
        )
        agent_name = p.get("agent_name")
        if agent_name and delta > 0:
            await safe_db.proj_sprint_usage_by_agent.update_one(
                {"agent_name": agent_name},
                {"$inc": {"tokens_used_input": input_tok,
                          "tokens_used_output": output_tok,
                          "invocations": 1},
                 "$set": {"last_seen": datetime.now(timezone.utc)},
                 "$setOnInsert": {"first_seen": datetime.now(timezone.utc)}},
                upsert=True,
            )
    elif t == "SprintCapAlert":
        threshold = int(p.get("threshold_pct", 0))
        # `last_alert_pct = GREATEST(last_alert_pct, threshold)` — we
        # emulate via a conditional update: only set if lower.
        row = await safe_db.proj_sprint_usage.find_one({})
        current = int((row or {}).get("last_alert_pct", 0))
        if threshold > current:
            await safe_db.proj_sprint_usage.update_one(
                {},
                {"$set": {"last_alert_pct": threshold,
                          "updated_by_event": event_id}},
                upsert=True,
            )
    # unknown types: no projection (silent by design)
