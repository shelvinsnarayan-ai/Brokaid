"""Client pending-quote surface — GET /api/c/policies/:policy_id/pending-quote (R5c-4b — Mongo-only)."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Request

from app.auth.sessions import Session
from app.db.tenant_safe import wrap
from app.kernel.tenant_ctx import use_tenant
from app.middleware.session import require_session
from app.routes._scrub import scrub


router = APIRouter(prefix="/api/c/policies")


def _parse(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


async def _find_latest_by_type_and_envelope(safe, type_: str, envelope_id: str):
    async for r in safe.events.find({"type": type_}).sort("seq", -1):
        p = r.get("payload") or {}
        if p.get("envelope_id") == envelope_id:
            return _parse(p)
    return None


async def _find_latest_by_type_and_policy(safe, type_: str, policy_id: str):
    async for r in safe.events.find({"type": type_}).sort("seq", -1):
        p = r.get("payload") or {}
        if p.get("policy_id") == policy_id:
            return _parse(p)
    return None


async def _pending(store, tenant_id: str, policy_id: str) -> dict[str, Any]:
    safe = wrap(store)
    async with use_tenant(tenant_id):
        ready = await _find_latest_by_type_and_policy(
            safe, "EndorsementQuoteReady", policy_id,
        )
        if not ready:
            return {"pending": False}
        envelope_id = ready.get("envelope_id")
        async for r in safe.events.find({"type": "EndorsementExecuted"}):
            p = r.get("payload") or {}
            if p.get("envelope_id") == envelope_id:
                return {"pending": False, "reason": "already_executed"}
        submission = await _find_latest_by_type_and_envelope(
            safe, "SubmissionDrafted", envelope_id,
        )
        quote = await _find_latest_by_type_and_envelope(
            safe, "CarrierQuoted", envelope_id,
        )
    return {"pending": True, "envelope_id": envelope_id,
            "submission": scrub(submission) if submission else None,
            "quote": scrub(quote) if quote else None}


@router.get("/{policy_id}/pending-quote")
async def pending_quote(
    policy_id: str,
    request: Request,
    session: Session = Depends(require_session),
) -> dict[str, Any]:
    body = await _pending(
        request.app.state.mongo_db, session.tenant_id, policy_id,
    )
    if not body.get("pending"):
        return {k: v for k, v in body.items() if k in ("pending", "reason")}
    qte = body["quote"]
    if not qte:
        return {"pending": False, "reason": "quote_missing"}
    sub = body["submission"]
    return {
        "pending": True,
        "envelope_id": body["envelope_id"],
        "policy_id": policy_id,
        "submission_summary": (sub or {}).get("submission_summary"),
        "structured_submission": (sub or {}).get("structured_submission"),
        "carrier": {
            "carrier_name": qte["carrier_name"],
            "premium_change_amount": qte["premium_change_amount"],
            "premium_new_annual": qte["premium_new_annual"],
            "premium_currency": qte["premium_currency"],
            "terms": qte["terms"],
            "conditions": qte["conditions"],
            "quoted_at": qte["quoted_at"],
            "mocked": True,
        },
    }
