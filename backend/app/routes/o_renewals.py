"""R1.6 — /api/o/renewals (pipeline, staff) and /api/c/renewals (client)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.db.tenant_safe import wrap
from app.kernel import holidays, payments
from app.kernel import renewals as rn
from app.kernel.authority import require_authority
from app.kernel.tenant_ctx import use_tenant
from app.kernel.test_data import test_client_ids
from app.kernel.trail_dispatch import append_via_dispatch
from app.middleware.client_guard import member_clients
from app.middleware.session import require_operator_session, require_session

router = APIRouter(prefix="/api/o", tags=["renewals"])
c_router = APIRouter(prefix="/api/c", tags=["renewals"])
ACT = ("ae", "principal")


def _store(request: Request):
    return request.app.state.mongo_db


def _ddmmyyyy(dt) -> str:
    return rn._utc(dt).astimezone(holidays.SYD).strftime("%d/%m/%Y")


def _n(v, basis="registry"):
    return {"value": v, "data_basis": basis}


async def _name(store, tid, uid) -> str:
    async with use_tenant(tid):
        return ((await wrap(store).users.find_one({"user_id": uid})) or {}).get("display_name") or "a broker"


async def _load(request, s, renewal_id) -> dict:
    try:
        return await rn.get(_store(request), s.tenant_id, renewal_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="renewal_not_found") from None


async def _run(coro):
    try:
        return await coro
    except rn.Bad as e:
        raise HTTPException(status_code=409, detail=str(e)) from None


def actor(s=Depends(require_operator_session)):
    if s.active_role not in ACT:
        raise HTTPException(status_code=403, detail="Only an AE or a Principal can work renewals.")
    return s


def _row(p, r, names, aes, now, state):
    exp = rn._utc(p["expiry"])
    days = (exp - now).days
    stage = r["stage"] if r else None
    overdue = bool(r and (r["stage"] == "lapse_risk" or (r.get("lapse_alerted") and stage not in rn.TERMINAL)
                          or rn.is_stuck(r, now, state))) or (not r and days < 0)
    ae = aes.get(p["client_id"])
    return {"policy_id": p["policy_id"], "client": names.get(p["client_id"], "Unknown client"), "policy": p.get("product"),
            "insurer": p.get("insurer_display"), "expiry": _ddmmyyyy(exp), "days_to_go": _n(max(days, 0)), "expired": days < 0,
            "promise": f"Renewal terms to client by {_ddmmyyyy(exp - timedelta(days=30))}",
            "next_step": NEXT_STEP.get(rn.cur(r) if r else "not_started", "—"), "renewal_id": (r or {}).get("renewal_id"),
            "stage": stage or "not_started", "stage_label": rn.LABEL.get(stage, "Not started"),
            "lapse_risk": bool(r and r.get("lapse_alerted") and stage not in rn.TERMINAL), "overdue": overdue,
            "needs_review": bool(r and r.get("needs_review")), "ae_user_id": ae, "ae": aes.get(f"name:{ae}") if ae else None,
            "advice_pending": bool(r and (r.get("recommendation") or {}).get("tier") == "P"
                                   and not r["recommendation"].get("principal_approved_by"))}


NEXT_STEP = {"not_started": "Starts automatically at the lead time (or start early)", "upcoming": "Send the client the renewal checklist",
             "info_requested": "Waiting for the client's details", "info_received": "Review the details, then go to market",
             "market": "Waiting for insurer quotes", "quotes_in": "Compare and write the recommendation",
             "recommendation_ready": "Send to the client (Principal approval if personal advice)",
             "client_decision": "Waiting for the client's choice and payment", "paid": "Waiting for the client's signature",
             "signed": "Binding", "bound": "Done — key the change packet into iAdviser", "lapsed": "Lapsed — confirm with the client",
             "declined": "Client declined — close off"}


@router.get("/renewals")
async def pipeline(request: Request, stage: str | None = None, ae: str | None = None, due: str | None = None,
                   s=Depends(require_operator_session)) -> dict:
    store, now = _store(request), datetime.now(timezone.utc)
    state = await holidays.state_for(store, s.tenant_id)
    safe = wrap(store)
    async with use_tenant(s.tenant_id):
        tids = await test_client_ids(safe)
        clients = [c async for c in safe.clients.find({}, {"client_id": 1, "display_name": 1, "assigned_ae_user_id": 1})]
        rs = {r["policy_id"]: r async for r in safe.renewals.find({"test": {"$ne": True}}, {"_id": 0})}
        pols = [p async for p in safe.policies.find({"expiry": {"$ne": None}, "client_id": {"$nin": tids}, "test": {"$ne": True},
                                                     "$or": [{"status": "managed"}, {"policy_id": {"$in": list(rs)}}]})]
        users = {u["user_id"]: u.get("display_name") async for u in safe.users.find({}, {"user_id": 1, "display_name": 1})}
    names = {c["client_id"]: c["display_name"] for c in clients}
    aes = {c["client_id"]: c.get("assigned_ae_user_id") for c in clients}
    aes |= {f"name:{u}": n for u, n in users.items()}
    rows = [_row(p, rs.get(p["policy_id"]), names, aes, now, state) for p in pols]
    rows = [r for r in rows if not (r["stage"] in rn.TERMINAL and r["expired"] and r["stage"] != "lapsed")]
    if stage:
        rows = [r for r in rows if r["stage"] == stage or (stage == "lapse_risk" and r["lapse_risk"])]
    if ae:
        rows = [r for r in rows if r["ae_user_id"] == ae]
    if due in ("30", "60", "90"):
        rows = [r for r in rows if not r["expired"] and r["days_to_go"]["value"] <= int(due)]
    elif due == "overdue":
        rows = [r for r in rows if r["overdue"]]
    rows.sort(key=lambda r: (not r["overdue"], tuple(reversed(r["expiry"].split("/")))))
    counts = {k: sum(1 for r in rows if r["stage"] == k) for k in ["not_started", *rn.LABEL]}
    return {"renewals": rows, "count": _n(len(rows)), "stage_counts": counts, "stages": rn.LABEL,
            "aes": sorted({(r["ae_user_id"], r["ae"]) for r in rows if r["ae_user_id"]}, key=lambda x: x[1] or ""),
            "settings": await holidays.tenant_settings(store, s.tenant_id)}


class SettingsBody(BaseModel):
    renewal_lead_days: int = Field(ge=45, le=90)
    holiday_state: str
    quoting_mode: str | None = None


@router.put("/renewals-settings")
async def put_settings(body: SettingsBody, request: Request, s=Depends(require_operator_session)) -> dict:
    if s.active_role != "operator":
        raise HTTPException(status_code=403, detail="Only the Operator can change renewal settings.")
    if body.holiday_state not in holidays.STATES:
        raise HTTPException(status_code=422, detail=f"State must be one of {', '.join(holidays.STATES)}.")
    store = _store(request)
    before = await holidays.tenant_settings(store, s.tenant_id)
    mode = body.quoting_mode or before["quoting_mode"]
    if mode not in ("mocked_allowed", "manual_only"):
        raise HTTPException(status_code=422, detail="Quoting mode must be mocked_allowed or manual_only.")
    if mode == "mocked_allowed" and not before["mocked_allowed_possible"]:
        raise HTTPException(status_code=422, detail="This is a real brokerage: mocked quotes can't be switched on.")
    new = {**body.model_dump(), "quoting_mode": mode}
    async with use_tenant(s.tenant_id):
        await wrap(store).tenant_settings.update_one({}, {"$set": new}, upsert=True)
    await append_via_dispatch(store, s.tenant_id, "TenantSettingsChanged", actor={"kind": "human", "id": s.user_id, "name": "operator"},
                              payload={"before": {k: before[k] for k in new}, "after": new},
                              idempotency_key=f"tenant_settings:{s.user_id}:{datetime.now(timezone.utc).isoformat()}")
    return {"settings": await holidays.tenant_settings(store, s.tenant_id)}


@router.get("/renewals/insurers")
async def insurer_list(request: Request, s=Depends(require_operator_session)) -> dict:
    return {"insurers": await rn.insurers(_store(request), s.tenant_id), "max": 3}


class StartBody(BaseModel):
    policy_id: str


@router.post("/renewals/start")
async def start_early(body: StartBody, request: Request, s=Depends(actor)) -> dict:
    store = _store(request)
    p = await rn._policy(store, s.tenant_id, body.policy_id)
    if not p or p.get("status") != "managed":
        raise HTTPException(status_code=404, detail="policy_not_found")
    if rn._utc(p["expiry"]) <= datetime.now(timezone.utc):
        raise HTTPException(status_code=409, detail="This policy has already expired.")
    return {"renewal": await rn.start(store, s.tenant_id, p, by=s.user_id, role=s.active_role)}


async def _detail(store, tid, r) -> dict:
    policy = await rn._policy(store, tid, r["policy_id"])
    pay = await rn.payable(store, tid, r)
    return {"renewal": {**r, "stage_label": rn.LABEL[r["stage"]], "current": rn.cur(r)}, "comparison": rn.comparison(r, policy),
            "payable": pay, "packet": rn.packet(r, policy, pay) if r.get("new_policy_id") else None,
            "general_advice_warning": rn.GENERAL_ADVICE_WARNING}


@router.get("/renewals/{renewal_id}")
async def detail(renewal_id: str, request: Request, s=Depends(require_operator_session)) -> dict:
    return await _detail(_store(request), s.tenant_id, await _load(request, s, renewal_id))


class NoteBody(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class QuotesBody(BaseModel):
    insurers: list[str]


class ManualBody(BaseModel):
    insurer: str
    premium: str
    excess: str | None = None
    key_terms: list[str] = []


class RecBody(BaseModel):
    quote_id: str
    text: str = Field(max_length=2000)
    reasons: list[str]


@router.post("/renewals/{renewal_id}/{action}")
async def act(renewal_id: str, action: str, request: Request, s=Depends(actor)) -> dict:
    store, tid = _store(request), s.tenant_id
    r = await _load(request, s, renewal_id)
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    kw = {"by": s.user_id, "role": s.active_role}
    if action == "request-info":
        await _run(rn.request_info(store, tid, r, **kw))
    elif action == "accept-info":
        await _run(rn.accept_info(store, tid, r, NoteBody(**body).note, **kw))
    elif action == "request-quotes":
        await _run(rn.request_quotes(store, tid, r, QuotesBody(**body).insurers, **kw))
    elif action == "manual-quote":
        await _run(rn.manual_quote(store, tid, r, ManualBody(**body).model_dump(), by_name=await _name(store, tid, s.user_id), **kw))
    elif action == "recommendation":
        b = RecBody(**body)
        await _run(rn.recommend(store, tid, r, b.quote_id, b.text, b.reasons, **kw))
    elif action == "approve-advice":
        if s.active_role != "principal":
            raise HTTPException(status_code=403, detail="Only a Principal can approve personal advice.")
        await _run(rn.approve_advice(store, tid, r, by=s.user_id))
    elif action == "send":
        await _run(rn.send_to_client(store, tid, r, **kw))
    else:
        raise HTTPException(status_code=404, detail="unknown_action")
    return await _detail(store, tid, await rn.get(store, tid, renewal_id))


@router.get("/renewals/{renewal_id}/change-packet.txt")
async def packet_txt(renewal_id: str, request: Request, s=Depends(require_operator_session)) -> PlainTextResponse:
    d = await _detail(_store(request), s.tenant_id, await _load(request, s, renewal_id))
    if not d["packet"]:
        raise HTTPException(status_code=409, detail="The change packet is ready once the renewal is bound.")
    return PlainTextResponse(d["packet"]["text"], headers={"Content-Disposition": f'attachment; filename="renewal-{renewal_id[:8]}.txt"'})


# ---- client ----
def _client(s=Depends(require_session)):
    if s.active_role not in ("client_owner", "client_contact"):
        raise HTTPException(status_code=403, detail="client_role_required")
    return s


def _client_view(r: dict, pay: dict | None, settings: dict | None, comp: dict, mode: str = "mocked_allowed") -> dict:
    rec = r.get("recommendation") if rn.released(r.get("recommendation")) else None
    stage = rn.cur(r) if r["stage"] != "lapse_risk" else r["stage"]
    out = {"renewal_id": r["renewal_id"], "policy_id": r["policy_id"], "product": r.get("product"),
           "insurer": r.get("insurer_display"), "expiry": _ddmmyyyy(r["expiry"]), "stage": stage, "current": rn.cur(r),
           "text": rn.CLIENT_TEXT[stage], "lapse_risk": bool(r.get("lapse_alerted") and r["stage"] not in rn.TERMINAL),
           "checklist": r["checklist"] if rn.cur(r) == "info_requested" else None, "decision": r.get("decision"),
           "new_policy_id": r.get("new_policy_id"), "signature": r.get("signature")}
    if rec:
        out["recommendation"] = {"quote_id": rec["quote_id"], "text": rec["text"], "reasons": rec["reasons"],
                                 "general_advice_warning": rec["general_advice_warning"]}
        out["options"] = [{k: o[k] for k in ("quote_id", "insurer", "premium", "premium_change", "excess", "excess_change",
                                             "limits_same", "key_differences", "mocked")} for o in comp["options"]]
        out["expiring"] = comp["expiring"]
    if pay:
        out["payment"] = payments.details(pay, settings, mode)
    out["esign_available"] = mode != "manual_only"
    return out


@c_router.get("/renewals")
async def my_renewals(request: Request, s=Depends(_client)) -> dict:
    store = _store(request)
    safe = wrap(store)
    async with use_tenant(s.tenant_id):
        mine = await member_clients(safe, s.user_id)
        rows = [r async for r in safe.renewals.find({"client_id": {"$in": list(mine)}, "stage": {"$ne": "upcoming"}}, {"_id": 0})
                .sort("expiry", 1)]
    return {"renewals": [{"renewal_id": r["renewal_id"], "policy_id": r["policy_id"], "new_policy_id": r.get("new_policy_id"),
                          "product": r.get("product"), "expiry": _ddmmyyyy(r["expiry"]), "stage": r["stage"],
                          "text": rn.CLIENT_TEXT[r["stage"]], "action_needed": rn.cur(r) in ("info_requested", "client_decision", "paid")}
                         for r in rows]}


async def _client_load(request, s, renewal_id):
    r = await _load(request, s, renewal_id)
    async with use_tenant(s.tenant_id):
        if r["client_id"] not in await member_clients(wrap(_store(request)), s.user_id):
            raise HTTPException(status_code=404, detail="not_found")
    return r


async def _client_detail(store, tid, r):
    pay = await rn.payable(store, tid, r)
    return _client_view(r, pay, await payments.get_settings(store, tid), rn.comparison(r, await rn._policy(store, tid, r["policy_id"])),
                        await holidays.quoting_mode(store, tid))


@c_router.get("/renewals/{renewal_id}")
async def my_renewal(renewal_id: str, request: Request, s=Depends(_client)) -> dict:
    return await _client_detail(_store(request), s.tenant_id, await _client_load(request, s, renewal_id))


class InfoBody(BaseModel):
    answers: dict


class DecideBody(BaseModel):
    choice: str = Field(max_length=60)
    reason: str | None = Field(default=None, max_length=500)


NEED = {"info": "can_request_change", "decide": "can_approve", "paid": "can_pay", "sign": "can_sign"}


@c_router.post("/renewals/{renewal_id}/info")
async def my_info(renewal_id: str, request: Request, s=Depends(_client)) -> dict:
    return await _my_act(renewal_id, "info", request, s)


@c_router.post("/renewals/{renewal_id}/decide")
async def my_decide(renewal_id: str, request: Request, s=Depends(_client)) -> dict:
    return await _my_act(renewal_id, "decide", request, s)


@c_router.post("/renewals/{renewal_id}/paid")
async def my_paid(renewal_id: str, request: Request, s=Depends(_client)) -> dict:
    return await _my_act(renewal_id, "paid", request, s)


@c_router.post("/renewals/{renewal_id}/sign")
async def my_sign(renewal_id: str, request: Request, s=Depends(_client)) -> dict:
    return await _my_act(renewal_id, "sign", request, s)


async def _my_act(renewal_id: str, action: str, request: Request, s) -> dict:
    store, tid = _store(request), s.tenant_id
    r = await _client_load(request, s, renewal_id)
    await require_authority(store, s, client_id=r["client_id"], action=f"renewal_{action}_attempt", needed=NEED[action],
                            policy_id=r["policy_id"], envelope_id=r["envelope_id"])
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    kw = {"by": s.user_id, "role": s.active_role}
    extra = {}
    if action == "info":
        await _run(rn.submit_info(store, tid, r, InfoBody(**body).answers, **kw))
    elif action == "decide":
        b = DecideBody(**body)
        await _run(rn.decide(store, tid, r, b.choice, b.reason, **kw))
    elif action == "paid":
        await _run(rn.advise_paid(store, tid, r, **kw))
    else:
        done = await _run(rn.sign(store, tid, r, **kw))
        extra = {"verifier_token": done.get("verifier_token")}
    return {**await _client_detail(store, tid, await rn.get(store, tid, renewal_id)), **extra}
