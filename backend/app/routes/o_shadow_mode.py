"""Shadow-mode route (R5c-4b — Mongo-only)."""
from __future__ import annotations

import csv
import io
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from app.auth.sessions import Session
from app.kernel import shadow_mode as shadow_mode_kernel
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

AGENT_DISPLAY: dict[str, str] = {
    "intake": "Intake", "placement": "Placement",
    "compliance": "Compliance", "liaison": "Liaison",
    "claims": "Claims stub", "unassigned": "Unassigned",
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


def _store(request: Request):
    s = getattr(request.app.state, "mongo_db", None)
    if s is None:
        raise HTTPException(status_code=503, detail="mongo_store_unavailable")
    return s


def _agent_for(class_key: str) -> str:
    return CLASS_TO_AGENT.get(class_key, "unassigned")


def _display_class(class_key: str) -> str:
    return CLASS_DISPLAY_FIX.get(class_key, class_key.replace("_", " ").title())


def _display_agent(agent_name: str) -> str:
    return AGENT_DISPLAY.get(agent_name, agent_name.title())


def _unlock_status(unlocked: bool, agreement_rate: float | None, eno_misses: int) -> str:
    if unlocked and eno_misses > 0:
        return "relocked_recently"
    if unlocked:
        return "unlocked"
    if agreement_rate is not None and agreement_rate >= 0.95 and eno_misses == 0:
        return "on_track"
    return "locked"


def _decorate_row(r: dict) -> dict:
    cls = r["class_key"]
    agent = _agent_for(cls)
    return {
        "agent_name": agent,
        "agent_display": _display_agent(agent),
        "class_key": cls,
        "class_display": _display_class(cls),
        "decisions_seen": r["decisions_seen"],
        "agreements": r["agreements"],
        "agreement_rate": r["agreement_rate"],
        "eno_misses": r["eno_misses"],
        "unlocked": r["unlocked"],
        "unlock_status": _unlock_status(
            r["unlocked"], r["agreement_rate"], r["eno_misses"],
        ),
        "first_decision_at": r["first_decision_at"],
        "last_decision_at": r["last_decision_at"],
    }


@router.get("/summary")
async def summary(
    request: Request,
    window: Literal["7d", "30d", "all"] = Query("30d"),
    session: Session = Depends(require_operator_session),
) -> dict:
    try:
        rows = await shadow_mode_kernel.summary_rows(
            _store(request), session.tenant_id, window,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    decorated = sorted(
        [_decorate_row(r) for r in rows],
        key=lambda x: (x["agent_name"], x["class_key"]),
    )
    return {"window": window, "rows": decorated}


@router.get("/decisions")
async def decisions(
    request: Request,
    agent: str = Query(...),
    class_key: str = Query(..., alias="class"),
    window: Literal["7d", "30d", "all"] = Query("30d"),
    limit: int = Query(50, ge=1, le=500),
    session: Session = Depends(require_operator_session),
) -> dict:
    if _agent_for(class_key) != agent:
        raise HTTPException(status_code=404, detail="agent_class_pair_unknown")
    try:
        pairs = await shadow_mode_kernel.paired_decisions(
            _store(request), session.tenant_id, class_key, window, limit=limit,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    shaped: list[dict] = []
    for p in pairs:
        approval_p = p["approval_payload"]
        matched_p = p.get("output_payload") or {}
        decision = str(approval_p.get("decision") or "unknown")
        edits = approval_p.get("edits") or {}
        edit_field_count = len(edits) if isinstance(edits, dict) else 0
        agreed = decision == "approve" or (
            decision == "edit" and edit_field_count <= 2
        )
        shaped.append({
            "envelope_id": p["envelope_id"],
            "ts": p["ts"],
            "proposal_summary": (
                (matched_p.get("output") or {}).get("summary")
                or "(no proposal event paired)"
            ),
            "human_decision": decision,
            "agreed": bool(agreed),
            "eno_flag": bool(approval_p.get("eno_flag", False)),
            "event_refs": {
                "agent_output_event_id": p.get("output_event_id"),
                "approval_event_id": p["approval_event_id"],
            },
        })
    return {
        "agent": agent, "agent_display": _display_agent(agent),
        "class_key": class_key, "class_display": _display_class(class_key),
        "window": window, "pairs": shaped,
    }


@router.get("/export")
async def export(
    request: Request,
    format: Literal["csv", "json"] = Query("csv"),
    window: Literal["7d", "30d", "all"] = Query("30d"),
    session: Session = Depends(require_operator_session),
) -> Response:
    try:
        rows = await shadow_mode_kernel.summary_rows(
            _store(request), session.tenant_id, window,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    decorated = sorted(
        [_decorate_row(r) for r in rows],
        key=lambda x: (x["agent_name"], x["class_key"]),
    )
    if format == "json":
        return Response(
            content=json.dumps({"window": window, "rows": decorated}, indent=2),
            media_type="application/json",
            headers={"Content-Disposition":
                     f"attachment; filename=shadow-mode_{window}.json"},
        )
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "agent_name", "class_key", "decisions_seen", "agreements",
        "agreement_rate", "eno_misses", "unlocked", "unlock_status",
        "last_decision_at",
    ])
    for r in decorated:
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
        headers={"Content-Disposition":
                 f"attachment; filename=shadow-mode_{window}.csv"},
    )
