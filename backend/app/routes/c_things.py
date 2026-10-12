"""R0.6 — client things (cards render from things + schedule_items)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.auth.sessions import Session
from app.db.tenant_safe import wrap
from app.kernel import things
from app.kernel.tenant_ctx import use_tenant
from app.middleware.session import require_session

router = APIRouter(prefix="/api/c")


@router.get("/things")
async def list_things(request: Request, session: Session = Depends(require_session)) -> dict:
    safe = wrap(request.app.state.mongo_db)
    async with use_tenant(session.tenant_id):
        link = await safe.user_client_links.find_one({"user_id": session.user_id})
        if not link:
            return {"things": []}
        rows = [t async for t in safe.things.find({"client_id": link["client_id"]})]
        pids = {i["policy_id"] for t in rows for i in t.get("schedule_items") or []}
        pols = {p["policy_id"]: p async for p in safe.policies.find({"policy_id": {"$in": list(pids)}})}
    order = {g: n for n, g in enumerate(things.GROUPS)}
    rows.sort(key=lambda t: (order.get(t["group"], 99), t["name"]))
    return {"things": [things.render(t, pols) for t in rows]}
