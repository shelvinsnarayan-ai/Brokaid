"""R1.8 — client claims: lodge (FNOL), list, detail, uploads, answer info requests, dispute a decision. Plus notifications."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import ConfigDict, BaseModel, Field

from app.auth.sessions import Session
from app.db.tenant_safe import wrap as _wrap_safe
from app.kernel import claims as ck
from app.kernel import compliance_docs, object_store
from app.kernel.authority import require_authority, client_for_session
from app.kernel.tenant_ctx import use_tenant
from app.middleware.session import require_session

router = APIRouter(prefix="/api/c")


class Party(BaseModel):
    model_config = ConfigDict(extra="forbid")  # R2.6: unknown fields are a 422
    name: str | None = Field(default=None, max_length=120)
    rego: str | None = Field(default=None, max_length=20)
    insurer: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)


class FNOLBody(BaseModel):
    model_config = ConfigDict(extra="forbid")  # R2.6: unknown fields are a 422
    policy_id: str | None = None
    claim_class: str = "other"
    subtype: str | None = None
    incident_date: str | None = Field(default=None, description="YYYY-MM-DD")
    incident_time: str | None = Field(default=None, max_length=10)
    where: str | None = Field(default=None, max_length=300)
    description: str = Field(..., min_length=8, max_length=4000)
    parties: list[Party] = Field(default_factory=list, max_length=5)
    police_report_no: str | None = Field(default=None, max_length=60)
    injuries: bool | None = None
    safe: bool | None = None
    rego: str | None = Field(default=None, max_length=20)


class TextBody(BaseModel):
    model_config = ConfigDict(extra="forbid")  # R2.6: unknown fields are a 422
    text: str = Field(..., min_length=1, max_length=4000)


def _store(request: Request):
    s = getattr(request.app.state, "mongo_db", None)
    if s is None:
        raise HTTPException(status_code=503, detail="mongo_store_unavailable")
    return s


async def _run(coro):
    try:
        return await coro
    except ck.Bad as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except LookupError:
        raise HTTPException(status_code=404, detail="claim_not_found") from None


async def _client(store, session) -> str:
    cid = await client_for_session(store, session)
    if not cid:
        raise HTTPException(status_code=404, detail="client_not_linked")
    return cid


async def _mine(store, session, claim_id) -> dict:
    cid = await _client(store, session)
    c = await _run(ck.get(store, session.tenant_id, claim_id))
    if c["client_id"] != cid:
        raise HTTPException(status_code=404, detail="claim_not_found")
    return c


async def _policy(store, tid, policy_id, client_id):
    if not policy_id:
        return None
    async with use_tenant(tid):
        p = await _wrap_safe(store).policies.find_one({"policy_id": policy_id, "client_id": client_id}, {"_id": 0})
    if not p:
        raise HTTPException(status_code=404, detail="policy_not_found")
    return p


@router.get("/claims/check")
async def check_policy(request: Request, policy_id: str, incident_date: str | None = None, rego: str | None = None,
                       claim_class: str = "motor", session: Session = Depends(require_session)) -> dict:
    store = _store(request)
    p = await _policy(store, session.tenant_id, policy_id, await _client(store, session))
    r = ck.policy_check(p, incident_date, rego, claim_class)
    vehicles = [{"rego": v.get("rego"), "label": f"{v.get('year') or ''} {v.get('make') or ''} {v.get('model') or ''} ({v.get('rego')})".strip()}
                for v in (p.get("schedule") or {}).get("vehicles") or []]
    return {**r, "vehicles": vehicles, "note": "These are checks for your broker, not a decision on your claim."}


@router.post("/claims")
async def submit_fnol(request: Request, body: FNOLBody, session: Session = Depends(require_session)) -> dict[str, Any]:
    store = _store(request)
    cid = await _client(store, session)
    await require_authority(store, session, client_id=cid, action="claim_attempt", needed="can_lodge_claim", policy_id=body.policy_id)
    policy = await _policy(store, session.tenant_id, body.policy_id, cid)
    data = body.model_dump()
    data["parties"] = [p for p in data["parties"]]
    c = await _run(ck.lodge(store, session.tenant_id, client_id=cid, policy=policy, body=data, by=session.user_id, role="client",
                            channel="in_app"))
    return {"state": "lodged", "claim_id": c["claim_id"], "claim_reference": c["claim_reference"],
            "message": ck.client_view(c, {})["status_text"], "ack_due": c["ack_due"].isoformat(), "excess": c.get("excess")}


@router.get("/claims")
async def list_claims(request: Request, session: Session = Depends(require_session)) -> dict[str, Any]:
    store = _store(request)
    cid = await _client(store, session)
    now = datetime.now(timezone.utc)
    async with use_tenant(session.tenant_id):
        rows = [c async for c in _wrap_safe(store).claims.find({"client_id": cid, "test": {"$ne": True}}, {"_id": 0})
                .sort("opened_at", -1).limit(100)]  # test-flagged claims never show to clients
    out = []
    for c in rows:
        if "stage" in c:
            out.append({**ck.row(c, now), "status_text": ck.client_text(c)})
        else:  # pre-R1.8 claim rows
            out.append({"claim_id": c["claim_id"], "claim_reference": c.get("claim_reference"), "stage": c.get("status"),
                        "stage_label": (c.get("status") or "").title(), "status_text": (c.get("description") or "")[:140],
                        "claim_class": c.get("claim_type") or "other", "subtype": "", "lodged_at": ck._iso(c.get("opened_at"))})
    return {"claims": out}


@router.get("/claims/{claim_id}")
async def get_claim(claim_id: str, request: Request, session: Session = Depends(require_session)) -> dict[str, Any]:
    store = _store(request)
    c = await _mine(store, session, claim_id)
    from app.kernel import repair_quote
    reply_ids = [d["reply_id"] for d in c.get("docs") or [] if d.get("reply_id")]
    async with use_tenant(session.tenant_id):
        hidden = {f["ref"] async for f in _wrap_safe(store).test_flags.find({"kind": "reply", "ref": {"$in": reply_ids}})} if reply_ids else set()
    from app.kernel import handled_by as hb
    step, by = hb.claim_next(c)
    return {"claim": {**ck.client_view(c, await compliance_docs.settings(store, session.tenant_id)),
                      "policy_status": await ck.policy_status(store, session.tenant_id, c.get("policy_id")),
                      "quote_summary": repair_quote.summary(c, hidden),
                      "handled_by": await hb.card(store, session.tenant_id, c["client_id"], next_step=step, by=by)}}


@router.post("/claims/{claim_id}/files")
async def upload(claim_id: str, request: Request, file: UploadFile = File(...), kind: str = Form("photo"),
                 keep_location: bool = Form(False), req_id: str | None = Form(None),
                 session: Session = Depends(require_session)) -> dict:
    store = _store(request)
    c = await _mine(store, session, claim_id)
    await require_authority(store, session, client_id=c["client_id"], action="claim_upload", needed="can_lodge_claim")
    doc = await _run(ck.add_file(store, session.tenant_id, c, kind=kind, data=await file.read(), filename=file.filename or "upload",
                                 content_type=file.content_type or "", keep_location=keep_location, by=session.user_id, role="client",
                                 req_id=req_id))
    return {"file": ck._doc_out(doc)}


@router.get("/claims/{claim_id}/files/{file_id}")
async def download(claim_id: str, file_id: str, request: Request, preview: bool = False, download: bool = False,
                   session: Session = Depends(require_session)) -> Response:
    store = _store(request)
    c = await _mine(store, session, claim_id)
    d = next((d for d in c.get("docs") or [] if d["file_id"] == file_id and d["client_visible"] and not d.get("test")), None)
    if not d or (preview and not d.get("preview_path")):
        raise HTTPException(status_code=404, detail="file_not_found")
    return await _file_response(d, request, preview=preview, download=download)


_CACHE: dict[str, bytes] = {}


async def _file_response(d: dict, request: Request, *, preview: bool = False, download: bool = False) -> Response:
    """R3.5: private claim file (or its JPEG preview) with HTTP Range support so phone browsers can play videos."""
    import re
    path = d["preview_path"] if preview else d["storage_path"]
    data = _CACHE.get(path)
    if data is None:
        data, _ = await object_store.get_object(path)
        if len(data) > 1024 * 1024:  # tiny per-process cache: video players fetch many ranges of the same file
            while len(_CACHE) >= 3:
                _CACHE.pop(next(iter(_CACHE)))
            _CACHE[path] = data
    ct_ = "image/jpeg" if preview else d["content_type"]
    name = re.sub(r"[^\w.\- ]", "_", ("preview-" + d["filename"] + ".jpg") if preview else d["filename"])
    h = {"Content-Disposition": f'{"attachment" if download else "inline"}; filename="{name}"', "Accept-Ranges": "bytes",
         "Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"}
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", request.headers.get("range") or "")
    if m and (m[1] or m[2]):
        n = len(data)
        start, end = (int(m[1]), int(m[2]) if m[2] else n - 1) if m[1] else (max(n - int(m[2]), 0), n - 1)
        end = min(end, n - 1)
        if start >= n or start > end:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{n}"})
        return Response(data[start:end + 1], status_code=206, media_type=ct_, headers={**h, "Content-Range": f"bytes {start}-{end}/{n}"})
    return Response(data, media_type=ct_, headers=h)


@router.post("/claims/{claim_id}/info/{req_id}")
async def answer(claim_id: str, req_id: str, body: TextBody, request: Request, session: Session = Depends(require_session)) -> dict:
    store = _store(request)
    c = await _mine(store, session, claim_id)
    await require_authority(store, session, client_id=c["client_id"], action="claim_info", needed="can_lodge_claim")
    c = await _run(ck.answer_info(store, session.tenant_id, c, req_id, body.text, by=session.user_id, role="client"))
    return {"claim": ck.client_view(c, await compliance_docs.settings(store, session.tenant_id))}


@router.post("/claims/{claim_id}/dispute")
async def dispute(claim_id: str, body: TextBody, request: Request, session: Session = Depends(require_session)) -> dict:
    store = _store(request)
    c = await _mine(store, session, claim_id)
    await require_authority(store, session, client_id=c["client_id"], action="claim_dispute", needed="can_lodge_claim")
    c = await _run(ck.dispute(store, session.tenant_id, c, reason=body.text, by=session.user_id, role="client"))
    return {"claim": ck.client_view(c, await compliance_docs.settings(store, session.tenant_id))}


@router.get("/notifications")
async def list_notifications(request: Request, session: Session = Depends(require_session)) -> dict[str, Any]:
    store = _store(request)
    cid = await _client(store, session)
    out: list[dict[str, Any]] = []
    async with use_tenant(session.tenant_id):
        async for r in _wrap_safe(store).proj_notifications.find({"client_id": cid}).sort("ts", -1).limit(50):
            out.append({"notification_id": r["notification_id"], "ts": ck._iso(r.get("ts")), "kind": r.get("kind"),
                        "subject": r.get("subject"), "body_text": r.get("body_text"), "read": r.get("read_at") is not None,
                        "source_event_id": r.get("source_event_id")})
    return {"notifications": out}
