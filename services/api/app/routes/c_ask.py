"""Client-facing Ask channel — POST /api/c/ask/message.

Runs liAIson to draft a reply → tries release() → returns the
release outcome + the draft (or a HITL-styled placeholder) so the
Ask UI can render structured bubbles.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth.sessions import Session
from app.kernel.agents.liaison import EmergentLLMProviderLiaison
from app.kernel.release import try_release
from app.kernel.trail import append
from app.middleware.session import require_session


router = APIRouter(prefix="/api/c/ask")


class AskBody(BaseModel):
    message: str


async def _load_client_and_broker(
    pool, tenant_id: str, session: Session,
) -> tuple[dict, str, str]:
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            # Client entity — pick the first linked to this user.
            client_row = await c.fetchrow(
                """
                SELECT cl.client_id::text as cid, cl.display_name
                  FROM user_client_links l
                  JOIN clients cl ON cl.client_id = l.client_id
                 WHERE l.user_id = $1::uuid
                 LIMIT 1
                """,
                session.user_id,
            )
            broker_row = await c.fetchrow(
                "SELECT display_name FROM brokerages "
                "WHERE tenant_id=$1::uuid LIMIT 1",
                tenant_id,
            )
    if not client_row:
        raise HTTPException(status_code=404, detail="client_not_linked")
    return (
        {"client_id": client_row["cid"], "display_name": client_row["display_name"]},
        client_row["display_name"],
        (broker_row["display_name"] if broker_row else "your broker"),
    )


async def _recent_events_summary(pool, tenant_id: str, client_id: str) -> str:
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
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


@router.post("/message")
async def send_message(
    request: Request,
    body: AskBody = Body(...),
    session: Session = Depends(require_session),
) -> dict[str, Any]:
    pool = request.app.state.pool
    tenant_id = session.tenant_id
    client, client_name, broker_name = await _load_client_and_broker(
        pool, tenant_id, session,
    )
    recent = await _recent_events_summary(pool, tenant_id, client["client_id"])

    envelope_id = str(uuid.uuid4())
    # Log the inbound message.
    await append(
        pool, tenant_id, "ClientQuestionAsked",
        actor={"kind": "human", "id": session.user_id, "name": "client_owner"},
        payload={
            "envelope_id": envelope_id,
            "client_id": client["client_id"],
            "message": body.message,
        },
        idempotency_key=f"ask:in:{envelope_id}",
    )

    # Run liAIson.
    liaison = EmergentLLMProviderLiaison()
    draft = await liaison.draft(
        client_message=body.message,
        context={
            "client_display_name": client_name,
            "linked_broker_name": broker_name,
            "matter_kind": "general",
            "recent_events_summary": recent,
            "policy_summary": None,
        },
    )
    # Release pipeline. If liAIson punted (coverage question), route to
    # comms_advisory HITL directly; try_release with the draft still
    # exercises the pipeline for provenance.
    release = await try_release(
        pool=pool,
        tenant_id=tenant_id,
        draft=draft["response_draft"],
        context={
            "matter_kind": "general",
            "client_context_summary": client_name,
            "recent_events_summary": recent,
        },
        author_agent="liaison",
        envelope_id=envelope_id,
        client_id=client["client_id"],
        mocked_channels_used=[],
    )
    return {
        "envelope_id": envelope_id,
        "released": release.released,
        "verdict": release.verdict,
        "draft": {
            "response_draft": draft.get("response_draft"),
            "broker_attribution": draft.get("broker_attribution"),
            "next_step": draft.get("next_step"),
            "detected_coverage_question": draft.get("detected_coverage_question"),
        },
        "classifier_verdict": (
            {
                "advice_classification":
                    release.classifier_verdict.get("advice_classification")
                    if release.classifier_verdict else None,
            }
            if release.classifier_verdict else None
        ),
    }


@router.get("/thread")
async def thread(
    request: Request, session: Session = Depends(require_session),
) -> dict[str, Any]:
    pool = request.app.state.pool
    tenant_id = session.tenant_id
    client, _cn, _bn = await _load_client_and_broker(pool, tenant_id, session)
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            rows = await c.fetch(
                """
                SELECT seq, ts, type, payload FROM events
                 WHERE tenant_id=$1::uuid
                   AND payload->>'client_id' = $2
                   AND type IN ('ClientQuestionAsked','ClientMessageReleased',
                                'ClientMessageHITL','ClientMessageRewriteRequired')
                 ORDER BY seq ASC LIMIT 40
                """,
                tenant_id, client["client_id"],
            )
    thread = []
    for r in rows:
        p = (
            json.loads(r["payload"]) if isinstance(r["payload"], str) else dict(r["payload"])
        )
        if r["type"] == "ClientQuestionAsked":
            thread.append({
                "who": "client",
                "text": p.get("message"),
                "ts": r["ts"].isoformat(timespec="microseconds"),
            })
        elif r["type"] == "ClientMessageReleased":
            thread.append({
                "who": "broker",
                "text": p.get("message"),
                "attribution": None,
                "next_step": None,
                "verified": (
                    (p.get("classifier_verdict") or {}).get("advice_classification") == "factual"
                ),
                "ts": r["ts"].isoformat(timespec="microseconds"),
            })
        else:
            thread.append({
                "who": "broker_pending",
                "text": "Your broker is drafting a response. Please keep an eye on this thread.",
                "ts": r["ts"].isoformat(timespec="microseconds"),
            })
    return {"thread": thread}
