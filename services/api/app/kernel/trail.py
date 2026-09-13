"""brokAId kernel — trAIl (ledger) surface.

Public API:
- create_tenant(pool, slug, display_name) -> Tenant
- append(pool, tenant_id, type, actor, payload, idempotency_key, mocked=False) -> Event
- read_events(pool, tenant_slug, from_seq=None, to_seq=None) -> list[Event]
- canonicalize / event_hash helpers via app.kernel.canonicalize

RLS: every DB connection this module opens sets `app.tenant_id` GUC
before touching events / proj_matters.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

import asyncpg

from app.kernel.canonicalize import event_hash, jcs

GENESIS_PREV = "0" * 64
_ADVISORY_LOCK_NAMESPACE = 0x62726F6B416964  # 'brokAId' as int, arbitrary


# Phase-10 invariant: every client-facing release event carries the
# compliance classifier verdict in its payload (belt-and-braces alongside
# the release pipeline). See app.kernel.release.try_release.
_CLIENT_RELEASE_EVENT_TYPES: frozenset[str] = frozenset({
    "ClientMessageReleased",
    "ClientMessageRewriteRequired",
    "ClientMessageHITL",
})


class ClassifierVerdictMissing(ValueError):
    """A ClientMessage* event was written without `classifier_verdict`."""


@dataclass
class Event:
    event_id: str
    tenant_id: str
    seq: int
    ts: str
    actor: dict
    type: str
    payload: dict
    payload_jcs: str
    prev_hash: str
    event_hash: str
    idempotency_key: str
    mocked: bool


@dataclass
class Tenant:
    tenant_id: str
    slug: str
    display_name: str
    genesis_hash: str


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


async def _set_tenant(conn: asyncpg.Connection, tenant_id: str) -> None:
    """Bind the RLS GUC for the current transaction."""
    await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)


async def _acquire_tenant_lock(conn: asyncpg.Connection, tenant_id: str) -> None:
    """Serialise inserts within a tenant via advisory xact lock."""
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended($1, $2::bigint))",
        tenant_id, _ADVISORY_LOCK_NAMESPACE,
    )


async def _last_hash(conn: asyncpg.Connection, tenant_id: str) -> str | None:
    row = await conn.fetchrow(
        "SELECT event_hash FROM events WHERE tenant_id = $1::uuid ORDER BY seq DESC LIMIT 1",
        tenant_id,
    )
    return row["event_hash"] if row else None


async def _project(conn: asyncpg.Connection, ev: Event) -> None:
    """In-transaction projection updater — dumb switch on event.type."""
    t = ev.type
    p = ev.payload
    if t == "MatterCreated":
        await conn.execute(
            """
            INSERT INTO proj_matters
              (tenant_id, matter_id, client_id, status, subject,
               updated_at, updated_by_event)
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7::uuid)
            ON CONFLICT (tenant_id, matter_id) DO UPDATE SET
              client_id = EXCLUDED.client_id,
              status    = EXCLUDED.status,
              subject   = EXCLUDED.subject,
              updated_at = EXCLUDED.updated_at,
              updated_by_event = EXCLUDED.updated_by_event
            """,
            ev.tenant_id, p["matter_id"], p["client_id"],
            p["status"], p["subject"], datetime.fromisoformat(ev.ts), ev.event_id,
        )
    elif t == "MatterStatusChanged":
        await conn.execute(
            """
            UPDATE proj_matters
               SET status = $3, updated_at = $4, updated_by_event = $5::uuid
             WHERE tenant_id = $1::uuid AND matter_id = $2::uuid
            """,
            ev.tenant_id, p["matter_id"], p["status"],
            datetime.fromisoformat(ev.ts), ev.event_id,
        )
    elif t == "WorkflowWait":
        await conn.execute(
            """
            INSERT INTO proj_workflow_waits
              (tenant_id, workflow_id, step, resume_predicate,
               fire_at, timeout_at, resolved, updated_by_event)
            VALUES ($1::uuid, $2, $3, $4::jsonb, $5, $6, FALSE, $7::uuid)
            ON CONFLICT (tenant_id, workflow_id, step) DO UPDATE SET
              resume_predicate = EXCLUDED.resume_predicate,
              fire_at = EXCLUDED.fire_at,
              timeout_at = EXCLUDED.timeout_at,
              updated_by_event = EXCLUDED.updated_by_event
            """,
            ev.tenant_id, p["workflow_id"], p["step"],
            json.dumps(p.get("resume_predicate", {})),
            _parse_ts(p.get("fire_at")), _parse_ts(p.get("timeout_at")),
            ev.event_id,
        )
    elif t == "WorkflowResumed" or t == "TimerFired":
        await conn.execute(
            """
            UPDATE proj_workflow_waits
               SET resolved = TRUE, resolved_at = $4,
                   updated_by_event = $5::uuid
             WHERE tenant_id = $1::uuid AND workflow_id = $2 AND step = $3
            """,
            ev.tenant_id, p["workflow_id"], p["step"],
            datetime.fromisoformat(ev.ts), ev.event_id,
        )
    elif t == "AgentOutputProduced":
        tokens = p.get("tokens_used") or {}
        input_tok = int(tokens.get("input", 0))
        output_tok = int(tokens.get("output", 0))
        delta = input_tok + output_tok
        await conn.execute(
            """
            INSERT INTO proj_sprint_usage
              (tenant_id, tokens_used, invocations,
               last_alert_pct, updated_by_event)
            VALUES ($1::uuid, $2, 1, 0, $3::uuid)
            ON CONFLICT (tenant_id) DO UPDATE SET
              tokens_used = proj_sprint_usage.tokens_used + EXCLUDED.tokens_used,
              invocations = proj_sprint_usage.invocations + 1,
              updated_by_event = EXCLUDED.updated_by_event
            """,
            ev.tenant_id, delta, ev.event_id,
        )
        agent_name = p.get("agent_name")
        if agent_name and delta > 0:
            await conn.execute(
                """
                INSERT INTO proj_sprint_usage_by_agent
                  (tenant_id, agent_name,
                   tokens_used_input, tokens_used_output,
                   invocations, first_seen, last_seen)
                VALUES ($1::uuid, $2, $3, $4, 1, NOW(), NOW())
                ON CONFLICT (tenant_id, agent_name) DO UPDATE SET
                  tokens_used_input =
                    proj_sprint_usage_by_agent.tokens_used_input + EXCLUDED.tokens_used_input,
                  tokens_used_output =
                    proj_sprint_usage_by_agent.tokens_used_output + EXCLUDED.tokens_used_output,
                  invocations = proj_sprint_usage_by_agent.invocations + 1,
                  last_seen = EXCLUDED.last_seen
                """,
                ev.tenant_id, agent_name, input_tok, output_tok,
            )
    elif t == "SprintCapAlert":
        await conn.execute(
            """
            UPDATE proj_sprint_usage
               SET last_alert_pct = GREATEST(last_alert_pct, $2),
                   updated_by_event = $3::uuid
             WHERE tenant_id = $1::uuid
            """,
            ev.tenant_id, int(p.get("threshold_pct", 0)), ev.event_id,
        )
    elif t == "ComplianceBlockAdded":
        await conn.execute(
            """
            INSERT INTO proj_active_blocks
              (tenant_id, block_id, target_kind, target_id,
               reason, added_by_event)
            VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6::uuid)
            ON CONFLICT (tenant_id, block_id) DO NOTHING
            """,
            ev.tenant_id, p["block_id"], p["target_kind"], p["target_id"],
            p.get("reason", ""), ev.event_id,
        )
    elif t == "ComplianceBlockCleared":
        await conn.execute(
            """
            UPDATE proj_active_blocks
               SET cleared_at = $3, cleared_reason = $4,
                   cleared_by_event = $5::uuid
             WHERE tenant_id = $1::uuid AND block_id = $2::uuid
            """,
            ev.tenant_id, p["block_id"],
            datetime.fromisoformat(ev.ts), p.get("reason", ""),
            ev.event_id,
        )
    # unknown types: no projection (silent by design)


def _parse_ts(v):
    if v is None:
        return None
    if isinstance(v, str):
        return datetime.fromisoformat(v)
    return v


async def append(
    pool: asyncpg.Pool,
    tenant_id: str,
    type: str,
    actor: dict,
    payload: dict,
    idempotency_key: str,
    mocked: bool = False,
    conn: asyncpg.Connection | None = None,
) -> Event:
    """Deterministically append an event to a tenant's chain.

    Uses an advisory transaction lock keyed on tenant_id so concurrent
    appends within a tenant serialise.  Returns the persisted Event.
    Raises asyncpg.UniqueViolationError on duplicate idempotency_key.
    """
    # Phase-10 belt-and-braces: reject any release event that would
    # write without the compliance classifier verdict. See
    # app.kernel.release.try_release which is the only code path that
    # should emit these types.
    if type in _CLIENT_RELEASE_EVENT_TYPES and "classifier_verdict" not in (payload or {}):
        raise ClassifierVerdictMissing(
            f"{type} events require 'classifier_verdict' in payload"
        )

    async def _do(c: asyncpg.Connection) -> Event:
        async with c.transaction():
            await _set_tenant(c, tenant_id)
            await _acquire_tenant_lock(c, tenant_id)
            prev = await _last_hash(c, tenant_id) or GENESIS_PREV

            event_id = str(uuid.uuid4())
            ts_dt = datetime.now(timezone.utc)
            ts_iso = ts_dt.isoformat(timespec="microseconds")
            payload_jcs = jcs(payload)
            eh = event_hash(
                event_id=event_id,
                ts_iso=ts_iso,
                tenant_id=tenant_id,
                actor=actor,
                type=type,
                payload_jcs=payload_jcs,
                prev_hash=prev,
            )

            await c.execute(
                """
                INSERT INTO events
                  (event_id, tenant_id, ts, actor, type, payload,
                   payload_jcs, prev_hash, event_hash, idempotency_key, mocked)
                VALUES
                  ($1::uuid, $2::uuid, $3, $4::jsonb, $5, $6::jsonb,
                   $7, $8, $9, $10, $11)
                """,
                event_id, tenant_id, ts_dt, json.dumps(actor), type,
                json.dumps(payload), payload_jcs, prev, eh,
                idempotency_key, mocked,
            )
            seq = await c.fetchval(
                "SELECT seq FROM events WHERE event_id = $1::uuid", event_id
            )

            ev = Event(
                event_id=event_id, tenant_id=tenant_id, seq=seq,
                ts=ts_iso, actor=actor, type=type, payload=payload,
                payload_jcs=payload_jcs, prev_hash=prev,
                event_hash=eh, idempotency_key=idempotency_key, mocked=mocked,
            )
            await _project(c, ev)
            return ev

    if conn is not None:
        return await _do(conn)
    async with pool.acquire() as c:
        return await _do(c)


async def create_tenant(
    pool: asyncpg.Pool, slug: str, display_name: str
) -> tuple[Tenant, Event]:
    """Create a tenant and its TenantGenesis event in one transaction.

    Genesis flow:
      1. INSERT tenants with placeholder genesis_hash = '0'*64.
      2. append() the TenantGenesis event (prev_hash = '0'*64).
      3. UPDATE tenants SET genesis_hash = <event.event_hash>.
    A BEFORE UPDATE trigger on tenants freezes genesis_hash after this
    initial move (any second UPDATE raises).
    """
    tenant_id = str(uuid.uuid4())
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO tenants (tenant_id, slug, display_name, genesis_hash) "
                "VALUES ($1::uuid, $2, $3, $4)",
                tenant_id, slug, display_name, GENESIS_PREV,
            )
            ev = await append(
                pool, tenant_id, "TenantGenesis",
                actor={"kind": "system", "id": "trail", "name": "trAIl"},
                payload={"slug": slug, "display_name": display_name},
                idempotency_key=f"genesis:{slug}",
                conn=conn,
            )
            await conn.execute(
                "UPDATE tenants SET genesis_hash = $1 WHERE tenant_id = $2::uuid",
                ev.event_hash, tenant_id,
            )
    return (
        Tenant(tenant_id=tenant_id, slug=slug,
               display_name=display_name, genesis_hash=ev.event_hash),
        ev,
    )


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


async def read_events(
    pool: asyncpg.Pool,
    tenant_ref: str,
    from_seq: int | None = None,
    to_seq: int | None = None,
) -> tuple[Tenant, list[Event]]:
    """Load a tenant + its events.

    `tenant_ref` may be either a slug (e.g. `burmac`) or a tenant_id UUID.
    Phase 15 operator endpoints pass the UUID (from session.tenant_id);
    the legacy `/api/trail/export?tenant=<slug>` path passes a slug.
    """
    async with pool.acquire() as conn:
        if _UUID_RE.match(tenant_ref):
            t = await conn.fetchrow(
                "SELECT tenant_id, slug, display_name, genesis_hash "
                "FROM tenants WHERE tenant_id = $1::uuid",
                tenant_ref,
            )
        else:
            t = await conn.fetchrow(
                "SELECT tenant_id, slug, display_name, genesis_hash "
                "FROM tenants WHERE slug = $1",
                tenant_ref,
            )
        if t is None:
            raise LookupError(f"unknown tenant: {tenant_ref}")
        tenant = Tenant(
            tenant_id=str(t["tenant_id"]), slug=t["slug"],
            display_name=t["display_name"], genesis_hash=t["genesis_hash"],
        )
        async with conn.transaction():
            await _set_tenant(conn, tenant.tenant_id)
            q = ("SELECT event_id, tenant_id, seq, ts, actor, type, payload, "
                 "payload_jcs, prev_hash, event_hash, idempotency_key, mocked "
                 "FROM events WHERE tenant_id = $1")
            args = [tenant.tenant_id]
            if from_seq is not None:
                q += f" AND seq >= ${len(args)+1}"
                args.append(from_seq)
            if to_seq is not None:
                q += f" AND seq <= ${len(args)+1}"
                args.append(to_seq)
            q += " ORDER BY seq ASC"
            rows = await conn.fetch(q, *args)

    events = [
        Event(
            event_id=str(r["event_id"]),
            tenant_id=str(r["tenant_id"]),
            seq=r["seq"],
            ts=r["ts"].isoformat(timespec="microseconds"),
            actor=json.loads(r["actor"]) if isinstance(r["actor"], str) else r["actor"],
            type=r["type"],
            payload=json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"],
            payload_jcs=r["payload_jcs"],
            prev_hash=r["prev_hash"],
            event_hash=r["event_hash"],
            idempotency_key=r["idempotency_key"],
            mocked=r["mocked"],
        )
        for r in rows
    ]
    return tenant, events


def event_to_json(ev: Event) -> dict[str, Any]:
    return {
        "event_id": ev.event_id,
        "tenant_id": ev.tenant_id,
        "seq": ev.seq,
        "ts": ev.ts,
        "actor": ev.actor,
        "type": ev.type,
        "payload": ev.payload,
        "payload_jcs": ev.payload_jcs,
        "prev_hash": ev.prev_hash,
        "event_hash": ev.event_hash,
        "idempotency_key": ev.idempotency_key,
        "mocked": ev.mocked,
    }
