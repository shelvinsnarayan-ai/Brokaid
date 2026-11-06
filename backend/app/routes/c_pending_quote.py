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
        client_change = await _find_latest_by_type_and_policy(
            safe, "ClientChangeRequested", policy_id,
        )
        if not ready and not client_change:
            return {"pending": False}
        # Prefer the ready envelope; if only the change request exists,
        # the client is still in-flight through intake / placement.
        envelope_id = (ready or client_change).get("envelope_id")
        newest_cc = await safe.events.find_one(
            {"type": "ClientChangeRequested", "payload.policy_id": policy_id},
            sort=[("seq", -1)],
        )
        newest_ready = await safe.events.find_one(
            {"type": "EndorsementQuoteReady", "payload.policy_id": policy_id},
            sort=[("seq", -1)],
        )
        if newest_cc and (not newest_ready or newest_cc["seq"] > newest_ready["seq"]):
            envelope_id = (newest_cc.get("payload") or {}).get("envelope_id")
        abandoned = await _find_latest_by_type_and_envelope(
            safe, "EnvelopeAbandoned", envelope_id,
        )
        if abandoned:
            return {"pending": False, "reason": "abandoned"}
        boundary = await _find_latest_by_type_and_envelope(
            safe, "AdviceBoundaryClassified", envelope_id,
        )
        if boundary and boundary.get("tier") == "X":
            return {"pending": True, "state": "declined",
                    "envelope_id": envelope_id, "reason": "declined"}
        review = await _find_latest_by_type_and_envelope(
            safe, "RequestNeedsHumanReview", envelope_id,
        )
        if review and await safe.events.find_one({"type": "OperatorApproval", "payload.envelope_id": envelope_id}):
            return {"pending": True, "state": "broker_reviewed", "envelope_id": envelope_id,
                    "reason": "broker_reviewed"}
        if review:
            return {
                "pending": True,
                "state": "awaiting_broker_review",
                "envelope_id": envelope_id,
                "reason": "awaiting_broker_review",
            }
        # Executed?
        async for r in safe.events.find({"type": "EndorsementExecuted"}):
            p = r.get("payload") or {}
            if p.get("envelope_id") == envelope_id:
                return {"pending": False, "reason": "already_executed"}
        # Gate on operator approval — per Build Brief §5B, the client
        # never sees a mocked carrier quote until the AE / principal
        # has approved it on the Control Tower.
        op_approval = await safe.events.find_one({
            "type": "OperatorApproval",
            "payload.envelope_id": envelope_id,
        })
        if op_approval is None:
            return {
                "pending": True,
                "state": "with_broker",
                "envelope_id": envelope_id,
                "reason": "with_broker",
            }
        submission = await _find_latest_by_type_and_envelope(
            safe, "SubmissionDrafted", envelope_id,
        )
        quote = await _find_latest_by_type_and_envelope(
            safe, "CarrierQuoted", envelope_id,
        )
    return {"pending": True, "state": "quote_ready",
            "envelope_id": envelope_id,
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
    if body.get("state") in ("with_broker", "awaiting_broker_review", "broker_reviewed", "declined"):
        from app.kernel import handled_by as hb
        async with use_tenant(session.tenant_id):
            pol = await wrap(request.app.state.mongo_db).policies.find_one({"policy_id": policy_id}, {"client_id": 1})
        step = None if body["state"] == "declined" else "Your broker reviews your change"
        return {
            "handled_by": await hb.card(request.app.state.mongo_db, session.tenant_id, (pol or {}).get("client_id"), next_step=step),
            "pending": True,
            "state": body["state"],
            "envelope_id": body["envelope_id"],
            "policy_id": policy_id,
        }
    qte = body["quote"]
    if not qte:
        return {"pending": False, "reason": "quote_missing"}
    sub = body["submission"]
    return {
        "pending": True,
        "state": "quote_ready",
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
