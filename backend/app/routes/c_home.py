"""R3.1 — /api/c/home, /api/c/activity, /api/c/policy/{id}: read-only client aggregates (bank-style app)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth.sessions import Session
from app.db.tenant_safe import wrap
from app.kernel import client_home as ch
from app.kernel import authority, holidays, payments
from app.kernel.tenant_ctx import use_tenant
from app.middleware.client_guard import member_clients
from app.middleware.session import require_session

router = APIRouter(prefix="/api/c", tags=["client-home"])
FILTERS = ("all", "payments", "claims", "changes", "renewals", "documents")


def _store(request: Request):
    return request.app.state.mongo_db


async def _ctx(request: Request, s: Session):
    if not s.client_id:
        raise HTTPException(status_code=403, detail="client_only")
    store = _store(request)
    async with use_tenant(s.tenant_id):
        if s.client_id not in await member_clients(wrap(store), s.user_id):
            raise HTTPException(status_code=403, detail="not_a_member")
    return store, await ch._ctx(store, s.tenant_id, s.client_id)


async def _broker(store, tid) -> dict:
    t = await store.tenants.find_one({"tenant_id": tid}, {"_id": 0, "display_name": 1, "email_settings": 1})  # tenant-safe: own tenant
    es = (t or {}).get("email_settings") or {}
    return {"name": es.get("sender_name") or (t or {}).get("display_name") or "Your broker", "email": es.get("reply_to"),
            "phone": es.get("phone")}


@router.get("/home")
async def home(request: Request, s: Session = Depends(require_session)) -> dict:
    store, ctx = await _ctx(request, s)
    now = datetime.now(timezone.utc)
    hour = now.astimezone(holidays.SYD).hour
    greeting = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
    if not ctx:
        return {"greeting": greeting, "client_name": None, "broker": await _broker(store, s.tenant_id), "attention": [], "cover": []}
    from app.routes.c_ask import unread as ask_unread
    from app.routes.c_next_action import _lookup
    try:
        unread = (await ask_unread(request, False, s))["count"]
    except HTTPException:
        unread = 0
    try:
        cert = await _lookup(store, s)
    except Exception:  # noqa: BLE001 — the certificate card is optional
        cert = None
    current = [p for p in ctx["policies"] if p.get("status") in ch.CURRENT]
    rem = ch.reminders(ctx, now.astimezone(holidays.SYD).date(), await _can_pay(store, s))
    reminded = set(ch.reminded_ids(ctx, now.astimezone(holidays.SYD).date())) if rem else set()  # the reminder replaces its attention card
    return {"greeting": greeting, "client_name": ctx["client"].get("display_name"), "broker": await _broker(store, s.tenant_id),
            "reminder": rem, "attention": [a for a in ch.attention(ctx, unread, cert, datetime.now(timezone.utc)) if a.get("payable_id") not in reminded],
            "cover": ch.cover_rows(current, ch._latest_renewals(ctx["renewals"]), now)}


async def _can_pay(store, s: Session) -> bool:
    return await authority.has(store, s, s.client_id, "can_pay")


@router.get("/activity")
async def activity(request: Request, f: str = "all", cursor: int = 0, s: Session = Depends(require_session)) -> dict:
    if f not in FILTERS:
        raise HTTPException(status_code=422, detail="unknown_filter")
    store, ctx = await _ctx(request, s)
    if not ctx:
        return {"items": [], "next_cursor": None, "due": [], "panel": None}
    items = [i for i in ch.activity_items(ctx) if f == "all" or i["type"] == f]
    page = items[cursor:cursor + ch.PAGE]
    due, side = [], None
    if cursor == 0:  # dues and the side panel ignore the feed filter
        due = ch.due_payments(ctx, await payments.get_settings(store, s.tenant_id), await holidays.quoting_mode(store, s.tenant_id))
        if not await _can_pay(store, s):  # no payment authority: details only, no pay action (same rule as the Home reminder)
            for d in due:
                d["pay_href"] = None
        side = ch.panel(ctx, datetime.now(timezone.utc))
    return {"items": page, "next_cursor": cursor + ch.PAGE if cursor + ch.PAGE < len(items) else None, "due": due, "panel": side}


@router.get("/policy/{policy_id}")
async def policy(policy_id: str, request: Request, s: Session = Depends(require_session)) -> dict:
    store, ctx = await _ctx(request, s)
    lead = (await holidays.tenant_settings(store, s.tenant_id))["renewal_lead_days"]
    page = ch.policy_page(ctx, policy_id, datetime.now(timezone.utc), lead) if ctx else None
    if not page:
        raise HTTPException(status_code=404, detail="not_found")
    return page
