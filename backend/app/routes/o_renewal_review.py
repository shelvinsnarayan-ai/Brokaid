"""R2.7 — /api/o/renewal-review: Operator acts, Principal reads, AE 403."""
from __future__ import annotations

import io
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from app.db.tenant_safe import wrap
from app.kernel import holidays, notify
from app.kernel import renewal_review as rv
from app.kernel import renewals as rn
from app.kernel.csvsafe import safe_writer
from app.kernel.tenant_ctx import use_tenant
from app.middleware.session import require_operator_session

router = APIRouter(prefix="/api/o", tags=["renewal-review"])
READ = ("operator", "principal")


def _store(request: Request):
    return request.app.state.mongo_db


def reader(s=Depends(require_operator_session)):
    if s.active_role not in READ:
        raise HTTPException(status_code=403, detail="operator_or_principal_only")
    return s


def operator(s=Depends(require_operator_session)):
    if s.active_role != "operator":
        raise HTTPException(status_code=403, detail="operator_only")
    return s


@router.get("/renewal-review")
async def review(request: Request, ae: str | None = None, stage: str | None = None, insurer: str | None = None, s=Depends(reader)) -> dict:
    d = await rv.review(_store(request), s.tenant_id, ae=ae, stage=stage, insurer=insurer)
    return {**d, "can_act": s.active_role == "operator"}


@router.get("/renewal-review/count")
async def count(request: Request, s=Depends(reader)) -> dict:
    return {"badge": (await rv.review(_store(request), s.tenant_id))["badge"]}


@router.get("/renewal-review.csv")
async def export(request: Request, ae: str | None = None, stage: str | None = None, insurer: str | None = None, s=Depends(reader)) -> Response:
    d = await rv.review(_store(request), s.tenant_id, ae=ae, stage=stage, insurer=insurer)
    buf = io.StringIO()
    w = safe_writer(buf)
    w.writerow(rv.CSV_HEADER)
    w.writerows(rv.csv_rows(d))
    day = datetime.now(timezone.utc).astimezone(holidays.SYD).strftime("%Y-%m-%d")
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="renewal-review-{day}.csv"'})


class NudgeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    renewal_id: str | None = None
    policy_id: str | None = None


@router.post("/renewal-review/nudge")
async def nudge(body: NudgeBody, request: Request, s=Depends(operator)) -> dict:
    """Alert the assigned AE (or the Principal + active AEs). Ledgered; once per renewal/policy per Sydney day."""
    store, safe = _store(request), wrap(_store(request))
    today = datetime.now(timezone.utc).astimezone(holidays.SYD).date().isoformat()
    async with use_tenant(s.tenant_id):
        if body.renewal_id:
            r = await safe.renewals.find_one({"renewal_id": body.renewal_id}, {"_id": 0})
            p = await safe.policies.find_one({"policy_id": (r or {}).get("policy_id")}, {"_id": 0}) if r else None
        else:
            r, p = None, await safe.policies.find_one({"policy_id": body.policy_id}, {"_id": 0}) if body.policy_id else None
        if not p:
            raise HTTPException(status_code=404, detail="not_found")
        key = r["renewal_id"] if r else f"policy:{p['policy_id']}"
        if await safe.events.find_one({"type": "RenewalNudgeSent", "payload.key": key, "payload.day": today}):
            raise HTTPException(status_code=429, detail="Already nudged today. Try again tomorrow.")
        client = await safe.clients.find_one({"client_id": p["client_id"]}) or {}
        ae = await notify.active_ae(safe, client.get("assigned_ae_user_id"))
        to = [ae] if ae else await notify._staff(safe, ("ae", "principal"))
    exp = rn._utc((r or p)["expiry"]).astimezone(holidays.SYD)
    link = f"/o/renewals?r={r['renewal_id']}" if r else f"/o/clients/{p['client_id']}"
    for uid in to:
        await notify.deliver(store, s.tenant_id, uid, kind="renewal_nudge", title=f"Renewal needs attention: {client.get('display_name') or 'client'}",
                             many="Renewals need attention", body=f"{p.get('product') or 'Policy'} expires {exp:%d/%m/%Y}. "
                             f"Stage: {rn.LABEL[rn.cur(r)] if r else 'Not started'}. The Operator asked you to look at it.",
                             link=link, dedupe_key=f"nudge:{key}", idem=f"nudge:{key}:{today}:{uid}")
    await rn._event(store, s.tenant_id, "RenewalNudgeSent", {"key": key, "day": today, "renewal_id": (r or {}).get("renewal_id"),
                    "policy_id": p["policy_id"], "client_id": p["client_id"], "to": to}, f"renewal_nudge:{key}:{today}", s.user_id, s.active_role)
    return {"ok": True, "sent_to": len(to)}
