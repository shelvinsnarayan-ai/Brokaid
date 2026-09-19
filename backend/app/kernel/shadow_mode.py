"""Shadow mode kernel — R5c-1b-β.

Extracted from `routes/o_shadow_mode.py`. Two read helpers:
- `summary_rows` — aggregates Approval events + proj_class_unlock.
- `paired_decisions` — pairs Approval events with upstream
  AgentOutputProduced via payload.envelope_id.

PG path byte-verbatim from pre-extraction route; Mongo path uses
TenantSafeDatabase under use_tenant. The class→agent map + display
helpers stay in the route module (no data-dispatch surface).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.tenant_safe import TenantSafeDatabase, wrap as _wrap_safe
from app.kernel.tenant_ctx import use_tenant


def _is_mongo(store) -> bool:
    return isinstance(store, (AsyncIOMotorDatabase, TenantSafeDatabase))


def parse_window(window: str) -> datetime | None:
    if window == "all":
        return None
    now = datetime.now(timezone.utc)
    if window == "7d":
        return now - timedelta(days=7)
    if window == "30d":
        return now - timedelta(days=30)
    raise ValueError("window must be 7d|30d|all")


async def summary_rows(store, tenant_id: str, window: str) -> list[dict]:
    lower_bound = parse_window(window)
    if _is_mongo(store):
        safe = _wrap_safe(store)
        ts_by_class: dict[str, tuple] = {}
        unlocks: list[dict] = []
        lb_iso = (
            lower_bound.isoformat(timespec="microseconds")
            if lower_bound is not None else None
        )
        async with use_tenant(tenant_id):
            query: dict[str, Any] = {"type": "Approval"}
            if lb_iso is not None:
                query["ts"] = {"$gte": lb_iso}
            async for r in safe.events.find(query):
                p = r.get("payload") or {}
                cls = p.get("class_key") or ""
                ts = r.get("ts")
                first, last = ts_by_class.get(cls, (None, None))
                if first is None or (ts is not None and ts < first):
                    first = ts
                if last is None or (ts is not None and ts > last):
                    last = ts
                ts_by_class[cls] = (first, last)
            async for r in safe.proj_class_unlock.find({}):
                unlocks.append(r)
        return _shape_summary(unlocks, ts_by_class)
    async with store.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            if lower_bound:
                rows = await c.fetch(
                    """
                    SELECT COALESCE(payload->>'class_key', '') AS class_key,
                           MIN(ts) AS first_ts, MAX(ts) AS last_ts
                      FROM events
                     WHERE tenant_id=$1::uuid AND type='Approval'
                       AND ts >= $2
                     GROUP BY 1
                    """,
                    tenant_id, lower_bound,
                )
            else:
                rows = await c.fetch(
                    """
                    SELECT COALESCE(payload->>'class_key', '') AS class_key,
                           MIN(ts) AS first_ts, MAX(ts) AS last_ts
                      FROM events
                     WHERE tenant_id=$1::uuid AND type='Approval'
                     GROUP BY 1
                    """,
                    tenant_id,
                )
            unlocks = await c.fetch(
                """
                SELECT class_key, decisions_seen, agreements, eno_misses, unlocked
                  FROM proj_class_unlock
                 WHERE tenant_id=$1::uuid
                """,
                tenant_id,
            )
    ts_by_class = {r["class_key"]: (r["first_ts"], r["last_ts"]) for r in rows}
    return _shape_summary([dict(u) for u in unlocks], ts_by_class)


def _shape_summary(unlocks: list[dict], ts_by_class: dict[str, tuple]) -> list[dict]:
    out: list[dict] = []
    for r in unlocks:
        cls = r.get("class_key") or ""
        if not cls:
            continue
        seen = int(r.get("decisions_seen") or 0)
        ag = int(r.get("agreements") or 0)
        rate = round(ag / seen, 4) if seen else None
        first_ts, last_ts = ts_by_class.get(cls, (None, None))
        out.append({
            "class_key": cls,
            "decisions_seen": seen,
            "agreements": ag,
            "agreement_rate": rate,
            "eno_misses": int(r.get("eno_misses") or 0),
            "unlocked": bool(r.get("unlocked")),
            "first_decision_at": (
                first_ts.isoformat(timespec="seconds")
                if first_ts and hasattr(first_ts, "isoformat") else first_ts
            ),
            "last_decision_at": (
                last_ts.isoformat(timespec="seconds")
                if last_ts and hasattr(last_ts, "isoformat") else last_ts
            ),
        })
    return out


async def paired_decisions(
    store, tenant_id: str, class_key: str, window: str, *, limit: int = 50,
) -> list[dict]:
    """Return raw pairs: [(approval_payload, matched_output_payload_or_None, approval_ts, approval_event_id, output_event_id), ...]

    The route module handles the eno_flag/agreed computation because
    it also owns the class↔agent display mapping.
    """
    lower_bound = parse_window(window)
    if _is_mongo(store):
        safe = _wrap_safe(store)
        approvals: list[dict] = []
        outputs: list[dict] = []
        lb_iso = (
            lower_bound.isoformat(timespec="microseconds")
            if lower_bound is not None else None
        )
        async with use_tenant(tenant_id):
            aq: dict[str, Any] = {
                "type": "Approval",
                "payload.class_key": class_key,
            }
            if lb_iso is not None:
                aq["ts"] = {"$gte": lb_iso}
            async for r in safe.events.find(aq).sort("ts", -1).limit(limit):
                approvals.append({
                    "event_id": r["event_id"], "ts": r["ts"],
                    "payload": r.get("payload") or {},
                })
            async for r in safe.events.find({
                "type": "AgentOutputProduced",
                "payload.class_key": class_key,
            }):
                outputs.append({
                    "event_id": r["event_id"], "ts": r["ts"],
                    "payload": r.get("payload") or {},
                })
    else:
        args: list[Any] = [tenant_id, class_key]
        approvals_sql = (
            "SELECT event_id::text AS event_id, ts, payload FROM events "
            " WHERE tenant_id=$1::uuid AND type='Approval' "
            "   AND payload->>'class_key' = $2 "
        )
        if lower_bound:
            approvals_sql += " AND ts >= $3"
            args.append(lower_bound)
        approvals_sql += " ORDER BY ts DESC LIMIT $%d" % (len(args) + 1)
        args.append(limit)
        async with store.acquire() as c:
            async with c.transaction():
                await c.execute(
                    "SELECT set_config('app.tenant_id', $1, true)", tenant_id,
                )
                app_rows = await c.fetch(approvals_sql, *args)
                out_rows = await c.fetch(
                    "SELECT event_id::text AS event_id, ts, payload FROM events "
                    " WHERE tenant_id=$1::uuid AND type='AgentOutputProduced' "
                    "   AND payload->>'class_key' = $2 ",
                    tenant_id, class_key,
                )
        approvals = [
            {"event_id": r["event_id"], "ts": r["ts"],
             "payload": json.loads(r["payload"]) if isinstance(r["payload"], str)
                        else (r["payload"] or {})}
            for r in app_rows
        ]
        outputs = [
            {"event_id": r["event_id"], "ts": r["ts"],
             "payload": json.loads(r["payload"]) if isinstance(r["payload"], str)
                        else (r["payload"] or {})}
            for r in out_rows
        ]
    out_by_env: dict[str, dict] = {}
    for o in outputs:
        env = o["payload"].get("envelope_id")
        if env and env not in out_by_env:
            out_by_env[env] = o
    pairs: list[dict] = []
    for a in approvals:
        env = a["payload"].get("envelope_id")
        matched = out_by_env.get(env or "")
        pairs.append({
            "envelope_id": env,
            "ts": (
                a["ts"].isoformat(timespec="seconds")
                if hasattr(a["ts"], "isoformat") else a["ts"]
            ),
            "approval_event_id": a["event_id"],
            "approval_payload": a["payload"],
            "output_event_id": (matched or {}).get("event_id"),
            "output_payload": (matched or {}).get("payload"),
        })
    return pairs
