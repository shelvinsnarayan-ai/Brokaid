"""GET /api/c/next-action — client-facing "one thing to do next".

Returns the single most-relevant pending envelope for the logged-in
client. This is the signal ClientHome renders when it exists; when
there's nothing pending the endpoint returns `{"next_action": null}`
and the tab falls back to the calm empty state.

Currently only surfaces `endorsement_awaiting_client_approval` — the
one class R7-Demo-Seed seeds for burmac. Extensible by class-key
allow-list.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.auth.sessions import Session
from app.db.tenant_safe import wrap
from app.kernel.tenant_ctx import use_tenant
from app.middleware.session import require_session


router = APIRouter(prefix="/api/c")

CLIENT_PENDING_CLASSES = ("endorsement_awaiting_client_approval",)


async def _lookup(store, session: Session) -> dict[str, Any] | None:
    if not session.client_id:
        return None
    safe = wrap(store)
    async with use_tenant(session.tenant_id):
        # AgentOutputProduced with a client-side class_key and matching
        # client_id / policy_id.
        event = await safe.events.find_one(
            {
                "type": "AgentOutputProduced",
                "payload.client_id": session.client_id,
                "payload.class_key": {"$in": list(CLIENT_PENDING_CLASSES)},
            },
            sort=[("ts", -1)],
        )
    if not event:
        return None
    p = event.get("payload") or {}
    return {
        "kind": p.get("class_key"),
        "envelope_id": p.get("envelope_id"),
        "policy_id": p.get("policy_id"),
        "summary": (p.get("output") or {}).get("summary"),
        "revised_premium_aud": (
            (p.get("output") or {}).get("fields") or {}
        ).get("revised_premium_aud"),
        "delta_aud": (
            (p.get("output") or {}).get("fields") or {}
        ).get("delta_aud"),
        "href": (
            f"/c/cover/{p.get('policy_id')}/pending"
            if p.get("policy_id") else None
        ),
        "mocked": True,
    }


@router.get("/next-action")
async def next_action(
    request: Request,
    session: Session = Depends(require_session),
) -> dict[str, Any]:
    payload = await _lookup(request.app.state.mongo_db, session)
    return {"next_action": payload}
