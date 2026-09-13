"""Phase 14 — Shadow Mode dashboard + export.

Records agent proposal vs human decision for every A2 (HITL) card,
aggregates per-agent/per-class agreement rates, exportable.

Data path
---------
Aggregation is anchored on `proj_class_unlock` (kept up-to-date by
trail._project on every Approval) plus the raw `Approval` events for
first/last timestamps and drill-through pairing. Each class is mapped
to an agent via a fixed CLASS_TO_AGENT table declared here.

No internal agent names ever leave this module. The API boundary
translates via `AGENT_DISPLAY` before returning payloads.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from app.auth.sessions import Session
from app.middleware.session import require_operator_session


router = APIRouter(prefix="/api/o/shadow-mode")


CLASS_TO_AGENT: dict[str, str] = {
    "request_info": "intake",
    "submit_to_market": "placement",
    "structure_risk": "placement",
    "endorsement_standard": "placement",
    "endorsement_execute_instructed_quoted": "placement",
    "certificate_issue": "placement",
    "renewal_pack_unchanged": "placement",
    "comms_service": "compliance",
    "comms_advisory": "compliance",
    "claim_admin": "claims",
}


# Public-safe display labels. Deny-list is scanned by CI over the
# compiled frontend bundle only, but we render everything client-side
# from these strings anyway. `unassigned` covers classes not yet
# mapped.
AGENT_DISPLAY: dict[str, str] = {
    "intake": "Intake",
    "placement": "Placement",
    "compliance": "Compliance",
    "liaison": "Liaison",
    "claims": "Claims stub",
    "unassigned": "Unassigned",
}

CLASS_DISPLAY_FIX: dict[str, str] = {
    "request_info": "Request info",
    "submit_to_market": "Submit to market",
    "structure_risk": "Structure risk",
    "endorsement_standard": "Endorsement (standard)",
    "endorsement_execute_instructed_quoted":
        "Endorsement · execute instructed",
    "certificate_issue": "Certificate issue",
    "renewal_pack_unchanged": "Renewal pack (unchanged)",
    "comms_service": "Comms (service)",
    "comms_advisory": "Comms (advisory)",
    "claim_admin": "Claim admin",
}


def _agent_for(class_key: str) -> str:
    return CLASS_TO_AGENT.get(class_key, "unassigned")


def _display_class(class_key: str) -> str:
    return CLASS_DISPLAY_FIX.get(class_key, class_key.replace("_", " ").title())


def _display_agent(agent_name: str) -> str:
    return AGENT_DISPLAY.get(agent_name, agent_name.title())


def _parse_window(window: str) -> datetime | None:
    """Return the lower-bound TS for a window filter, or None for 'all'."""
    if window == "all":
        return None
    now = datetime.now(timezone.utc)
    if window == "7d":
        return now - timedelta(days=7)
    if window == "30d":
        return now - timedelta(days=30)
    raise HTTPException(status_code=400, detail="window must be 7d|30d|all")


def _unlock_status(
    unlocked: bool, agreement_rate: float | None, eno_misses: int
) -> str:
    if unlocked and eno_misses > 0:
        return "relocked_recently"
    if unlocked:
        return "unlocked"
    if agreement_rate is not None and agreement_rate >= 0.95 and eno_misses == 0:
        return "on_track"
    return "locked"


async def _summary_rows(pool, tenant_id: str, window: str) -> list[dict]:
    lower_bound = _parse_window(window)
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            # Aggregate approval events for first/last decision timestamps.
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
    out: list[dict] = []
    for r in unlocks:
        cls = r["class_key"]
        if not cls:
            continue
        seen = int(r["decisions_seen"] or 0)
        ag = int(r["agreements"] or 0)
        rate = round(ag / seen, 4) if seen else None
        first_ts, last_ts = ts_by_class.get(cls, (None, None))
        agent = _agent_for(cls)
        status = _unlock_status(bool(r["unlocked"]), rate, int(r["eno_misses"] or 0))
        out.append({
            "agent_name": agent,
            "agent_display": _display_agent(agent),
            "class_key": cls,
            "class_display": _display_class(cls),
            "decisions_seen": seen,
            "agreements": ag,
            "agreement_rate": rate,
            "eno_misses": int(r["eno_misses"] or 0),
            "unlocked": bool(r["unlocked"]),
            "unlock_status": status,
            "first_decision_at": first_ts.isoformat(timespec="seconds") if first_ts else None,
            "last_decision_at": last_ts.isoformat(timespec="seconds") if last_ts else None,
        })
    out.sort(key=lambda x: (x["agent_name"], x["class_key"]))
    return out


@router.get("/summary")
async def summary(
    request: Request,
    window: Literal["7d", "30d", "all"] = Query("30d"),
    session: Session = Depends(require_operator_session),
) -> dict:
    rows = await _summary_rows(request.app.state.pool, session.tenant_id, window)
    return {"window": window, "rows": rows}


@router.get("/decisions")
async def decisions(
    request: Request,
    agent: str = Query(...),
    class_key: str = Query(..., alias="class"),
    window: Literal["7d", "30d", "all"] = Query("30d"),
    limit: int = Query(50, ge=1, le=500),
    session: Session = Depends(require_operator_session),
) -> dict:
    """Paired-decision drill-through. Pairs Approval events with their
    upstream AgentOutputProduced (matched by payload.envelope_id).
    """
    if _agent_for(class_key) != agent:
        raise HTTPException(
            status_code=404, detail="agent_class_pair_unknown",
        )
    lower_bound = _parse_window(window)
    pool = request.app.state.pool
    tenant_id = session.tenant_id
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            approvals_sql = """
                SELECT event_id::text AS event_id, ts, payload
                  FROM events
                 WHERE tenant_id=$1::uuid AND type='Approval'
                   AND payload->>'class_key' = $2
            """
            args: list[Any] = [tenant_id, class_key]
            if lower_bound:
                approvals_sql += " AND ts >= $3"
                args.append(lower_bound)
            approvals_sql += " ORDER BY ts DESC LIMIT $%d" % (len(args) + 1)
            args.append(limit)
            approvals = await c.fetch(approvals_sql, *args)

            outputs = await c.fetch(
                """
                SELECT event_id::text AS event_id, ts, payload
                  FROM events
                 WHERE tenant_id=$1::uuid AND type='AgentOutputProduced'
                   AND payload->>'class_key' = $2
                """,
                tenant_id, class_key,
            )
    out_by_env: dict[str, dict] = {}
    for o in outputs:
        p = (
            json.loads(o["payload"]) if isinstance(o["payload"], str)
            else (o["payload"] or {})
        )
        env = p.get("envelope_id")
        if env and env not in out_by_env:
            out_by_env[env] = {"event_id": o["event_id"], "ts": o["ts"], "payload": p}

    pairs: list[dict] = []
    for a in approvals:
        p = (
            json.loads(a["payload"]) if isinstance(a["payload"], str)
            else (a["payload"] or {})
        )
        env = p.get("envelope_id")
        matched = out_by_env.get(env or "")
        decision = str(p.get("decision") or "unknown")
        edits = p.get("edits") or {}
        edit_field_count = len(edits) if isinstance(edits, dict) else 0
        agreed = decision == "approve" or (
            decision == "edit" and edit_field_count <= 2
        )
        pairs.append({
            "envelope_id": env,
            "ts": a["ts"].isoformat(timespec="seconds"),
            "proposal_summary": (
                ((matched or {}).get("payload") or {}).get("output", {}).get("summary")
                or "(no proposal event paired)"
            ),
            "human_decision": decision,
            "agreed": bool(agreed),
            "eno_flag": bool(p.get("eno_flag", False)),
            "event_refs": {
                "agent_output_event_id": (matched or {}).get("event_id"),
                "approval_event_id": a["event_id"],
            },
        })
    return {
        "agent": agent,
        "agent_display": _display_agent(agent),
        "class_key": class_key,
        "class_display": _display_class(class_key),
        "window": window,
        "pairs": pairs,
    }


@router.get("/export")
async def export(
    request: Request,
    format: Literal["csv", "json"] = Query("csv"),
    window: Literal["7d", "30d", "all"] = Query("30d"),
    session: Session = Depends(require_operator_session),
) -> Response:
    rows = await _summary_rows(request.app.state.pool, session.tenant_id, window)
    if format == "json":
        return Response(
            content=json.dumps({"window": window, "rows": rows}, indent=2),
            media_type="application/json",
            headers={
                "Content-Disposition":
                    f"attachment; filename=shadow-mode_{window}.json"
            },
        )
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "agent_name", "class_key", "decisions_seen", "agreements",
        "agreement_rate", "eno_misses", "unlocked", "unlock_status",
        "last_decision_at",
    ])
    for r in rows:
        w.writerow([
            r["agent_name"], r["class_key"], r["decisions_seen"],
            r["agreements"],
            "" if r["agreement_rate"] is None else f"{r['agreement_rate']:.4f}",
            r["eno_misses"], "true" if r["unlocked"] else "false",
            r["unlock_status"],
            r["last_decision_at"] or "",
        ])
    return Response(
        content=buf.getvalue(), media_type="text/csv",
        headers={
            "Content-Disposition":
                f"attachment; filename=shadow-mode_{window}.csv"
        },
    )
