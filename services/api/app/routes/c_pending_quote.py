"""Client pending-quote surface — GET /api/c/policies/:policy_id/pending-quote.

Returns the latest EndorsementQuoteReady for the given policy that has
no downstream EndorsementExecuted event, together with the linked
SubmissionDrafted (plain-English broker summary) and CarrierQuoted
(mocked carrier response). Used by /c/cover/:policy_id/pending.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth.sessions import Session
from app.middleware.session import require_session
from app.routes._scrub import scrub


router = APIRouter(prefix="/api/c/policies")


def _parse(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        try: return json.loads(raw)
        except Exception: return {}
    return {}


@router.get("/{policy_id}/pending-quote")
async def pending_quote(
    policy_id: str,
    request: Request,
    session: Session = Depends(require_session),
) -> dict[str, Any]:
    pool = request.app.state.pool
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", session.tenant_id)
            ready = await c.fetchrow(
                """
                SELECT payload FROM events
                 WHERE tenant_id=$1::uuid AND type='EndorsementQuoteReady'
                   AND payload->>'policy_id' = $2
                 ORDER BY seq DESC LIMIT 1
                """,
                session.tenant_id, policy_id,
            )
            if not ready:
                return {"pending": False}
            r_pl = _parse(ready["payload"])
            envelope_id = r_pl.get("envelope_id")

            executed = await c.fetchrow(
                "SELECT 1 FROM events "
                " WHERE tenant_id=$1::uuid AND type='EndorsementExecuted' "
                "   AND payload->>'envelope_id' = $2",
                session.tenant_id, envelope_id,
            )
            if executed:
                return {"pending": False, "reason": "already_executed"}

            submission = await c.fetchrow(
                "SELECT payload FROM events "
                " WHERE tenant_id=$1::uuid AND type='SubmissionDrafted' "
                "   AND payload->>'envelope_id' = $2 "
                " ORDER BY seq DESC LIMIT 1",
                session.tenant_id, envelope_id,
            )
            quote = await c.fetchrow(
                "SELECT payload FROM events "
                " WHERE tenant_id=$1::uuid AND type='CarrierQuoted' "
                "   AND payload->>'envelope_id' = $2 "
                " ORDER BY seq DESC LIMIT 1",
                session.tenant_id, envelope_id,
            )

    sub = scrub(_parse(submission["payload"])) if submission else None
    qte = scrub(_parse(quote["payload"])) if quote else None
    if not qte:
        return {"pending": False, "reason": "quote_missing"}
    return {
        "pending": True,
        "envelope_id": envelope_id,
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
