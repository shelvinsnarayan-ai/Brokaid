"""R1.7 — /api/o/compliance (staff) and client compliance routes (documents, complaints, My data)."""
from __future__ import annotations

import io
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field

from app.kernel.csvsafe import safe_writer
from app.db.tenant_safe import wrap
from app.kernel import complaints as cm
from app.kernel import compliance_docs as cd
from app.kernel import compliance_library as lib
from app.kernel import jobs, object_store, retention
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch
from app.middleware.client_guard import member_clients
from app.middleware.session import require_operator_session, require_session

router = APIRouter(prefix="/api/o/compliance", tags=["compliance"])
c_router = APIRouter(prefix="/api/c", tags=["compliance"])
STAFF = ("ae", "principal", "operator")


def _store(request: Request):
    return request.app.state.mongo_db


def _staff(s=Depends(require_operator_session)):
    if s.active_role not in STAFF:
        raise HTTPException(status_code=403, detail="staff_only")
    return s


def _lead(s=Depends(require_operator_session)):
    if s.active_role not in ("principal", "operator"):
        raise HTTPException(status_code=403, detail="Only a Principal or the Operator can do this.")
    return s


def _principal(s=Depends(require_operator_session)):
    if s.active_role != "principal":
        raise HTTPException(status_code=403, detail="Only a Principal can do this.")
    return s


async def _run(coro):
    try:
        return await coro
    except (cm.Bad, cd.Missing, ValueError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except LookupError:
        raise HTTPException(status_code=404, detail="not_found") from None


# ---- settings ----
class SettingsBody(BaseModel):
    gaw_text: str | None = Field(default=None, max_length=2000)
    afca_text: str | None = Field(default=None, max_length=2000)
    privacy_policy_text: str | None = Field(default=None, max_length=6000)
    collection_notice_text: str | None = Field(default=None, max_length=3000)
    retention_years: dict[str, int] | None = None


@router.get("/settings")
async def get_settings(request: Request, s=Depends(_staff)) -> dict:
    return {"settings": await cd.settings(_store(request), s.tenant_id),
            "retention_years": await retention.policy_years(_store(request), s.tenant_id)}


@router.put("/settings")
async def put_settings(body: SettingsBody, request: Request, s=Depends(_lead)) -> dict:
    new = {k: v for k, v in body.model_dump().items() if v is not None}
    if any(not 1 <= y <= 50 for y in (new.get("retention_years") or {}).values()):
        raise HTTPException(status_code=422, detail="Retention years must be 1–50.")
    store = _store(request)
    async with use_tenant(s.tenant_id):
        await wrap(store).tenant_settings.update_one({}, {"$set": new}, upsert=True)
    await append_via_dispatch(store, s.tenant_id, "TenantSettingsChanged", actor={"kind": "human", "id": s.user_id, "name": s.active_role},
                              payload={"changed": sorted(new)}, idempotency_key=f"compliance_settings:{datetime.now(timezone.utc).isoformat()}")
    return await get_settings(request, s)


# ---- documents ----
@router.get("/documents")
async def list_docs(request: Request, s=Depends(_staff)) -> dict:
    async with use_tenant(s.tenant_id):
        rows = [d async for d in wrap(_store(request)).disclosure_docs.find({}, {"_id": 0, "storage_path": 0}).sort("uploaded_at", -1)]
    return {"documents": rows, "kinds": cd.KINDS}


@router.post("/documents")
async def upload_doc(request: Request, file: UploadFile = File(...), kind: str = Form(...), version: str = Form(...),
                     effective_date: str = Form(...), insurer: str | None = Form(None), product: str | None = Form(None),
                     s=Depends(_lead)) -> dict:
    if kind == "FSG":  # R2.0: our own FSG only goes live via the library (draft → Principal approval → live)
        raise HTTPException(status_code=409, detail="Add the FSG through the compliance library: upload the final PDF on its draft, "
                                                    "have a Principal approve it, then make it live.")
    data = await file.read()
    if not data or len(data) > 20 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="Upload a file up to 20 MB.")
    doc = await _run(cd.upload(_store(request), s.tenant_id, kind=kind, data=data, filename=file.filename or "document.pdf",
                               content_type=file.content_type or "application/pdf", version=version, effective_date=effective_date,
                               insurer=insurer, product=product, by=s.user_id))
    doc.pop("storage_path", None)
    return {"doc": doc}


# ---- R2.0 compliance library: draft → approved by a Principal → live ----
async def _lib(coro):
    try:
        return await coro
    except lib.Bad as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from None


class SectionBody(BaseModel):
    body: str = Field(min_length=10, max_length=20000)


@router.get("/business-details")
async def get_business_details(request: Request, s=Depends(_lead)) -> dict:
    from app.kernel import business_details as bd
    from app.kernel import sandbox
    try:
        await sandbox.require(_store(request), s.tenant_id)
        is_sandbox = True
    except sandbox.NotSandbox:
        is_sandbox = False
    return {"details": await bd.current(_store(request), s.tenant_id), "sandbox": is_sandbox,
            "fields": [{"key": k, "label": lab, "help": h, "max": n} for k, lab, h, n in bd.FIELDS]}


@router.post("/sandbox/reset")
async def sandbox_reset(request: Request, s=Depends(_lead)) -> dict:
    """R2.4: burmac-sandbox (TEST tenant, dev mode) only — 404 for every other tenant."""
    from app.kernel import sandbox
    try:
        return await sandbox.reset(_store(request), s.tenant_id, by=s.user_id, role=s.active_role)
    except sandbox.NotSandbox:
        raise HTTPException(status_code=404, detail="not_found") from None


@router.put("/business-details")
async def put_business_details(body: dict, request: Request, s=Depends(_lead)) -> dict:
    """R2.3: validated, versioned (BusinessDetailsUpdated), fills the tenant record and regenerates every non-live draft."""
    from app.kernel import business_details as bd
    if not isinstance(body, dict) or any(not isinstance(v, str) for v in body.values()):
        raise HTTPException(status_code=422, detail="Send each field as text.")
    try:
        row = await bd.save(_store(request), s.tenant_id, body, by=s.user_id, role=s.active_role)
    except bd.Invalid as e:
        raise HTTPException(status_code=422, detail={"errors": e.errors}) from None
    regenerated = await lib.regenerate_all(_store(request), s.tenant_id, by=s.user_id, role=s.active_role) if row.get("changed") else []
    return {"details": row, "regenerated": regenerated}


class ApproveBody(BaseModel):
    reviewer_name: str = Field(min_length=2, max_length=120)
    confirmed: bool


@router.get("/library")
async def library(request: Request, s=Depends(_staff)) -> dict:
    rows = await lib.listing(_store(request), s.tenant_id)
    for r in rows:
        for k in ("docx_path", "pdf_path", "final_path", "text"):
            r.pop(k, None)
    return {"documents": rows, "confirmation": lib.CONFIRM}


@router.post("/library/drafts")
async def library_drafts(request: Request, s=Depends(_lead)) -> dict:
    made = await lib.generate_drafts(_store(request), s.tenant_id, by=s.user_id, role=s.active_role)
    return {"created": [m["kind"] for m in made]}


@router.get("/library/drafts.zip")
async def library_zip(request: Request, s=Depends(_lead)):
    return Response(await lib.drafts_zip(_store(request), s.tenant_id), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="compliance-drafts.zip"'})


@router.get("/library/{doc_id}")
async def library_doc(doc_id: str, request: Request, s=Depends(_staff)) -> dict:
    d = await _lib(lib.get(_store(request), s.tenant_id, doc_id))
    return {k: v for k, v in d.items() if not k.endswith("_path")}


@router.get("/library/{doc_id}/download")
async def library_download(doc_id: str, request: Request, fmt: str = "pdf", s=Depends(_staff)):
    data, ct, name = await _lib(lib.download(_store(request), s.tenant_id, doc_id, fmt if fmt in ("pdf", "docx", "final") else "pdf"))
    return Response(data, media_type=ct, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.put("/library/{doc_id}/sections/{index}")
async def library_section(doc_id: str, index: int, body: SectionBody, request: Request, s=Depends(_lead)) -> dict:
    d = await _lib(lib.edit_section(_store(request), s.tenant_id, doc_id, index, body.body, by=s.user_id, role=s.active_role))
    return {k: v for k, v in d.items() if not k.endswith("_path")}


@router.get("/library/{doc_id}/versions")
async def library_versions(doc_id: str, request: Request, s=Depends(_lead)) -> dict:
    return {"versions": await _lib(lib.versions(_store(request), s.tenant_id, doc_id))}


@router.get("/library/{doc_id}/diff")
async def library_diff(doc_id: str, a: int, b: int, request: Request, s=Depends(_lead)) -> dict:
    return {"a": a, "b": b, "lines": await _lib(lib.diff(_store(request), s.tenant_id, doc_id, a, b))}


@router.post("/library/{doc_id}/final")
async def library_final(doc_id: str, request: Request, file: UploadFile = File(...), s=Depends(_lead)) -> dict:
    data = await file.read(20 * 1024 * 1024 + 1)
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Upload a file up to 20 MB.")
    d = await _lib(lib.upload_final(_store(request), s.tenant_id, doc_id, data, by=s.user_id, role=s.active_role))
    return {k: v for k, v in d.items() if not k.endswith("_path")}


@router.post("/library/{doc_id}/approve")
async def library_approve(doc_id: str, body: ApproveBody, request: Request, s=Depends(_principal)) -> dict:
    d = await _lib(lib.approve(_store(request), s.tenant_id, doc_id, reviewer_name=body.reviewer_name, confirmed=body.confirmed,
                               by=s.user_id, role=s.active_role))
    return {k: v for k, v in d.items() if not k.endswith("_path")}


@router.post("/library/{doc_id}/live")
async def library_live(doc_id: str, request: Request, s=Depends(_lead)) -> dict:
    d = await _lib(lib.make_live(_store(request), s.tenant_id, doc_id, by=s.user_id, role=s.active_role))
    return {k: v for k, v in d.items() if not k.endswith("_path")}


# ---- client flags ----
class TypeBody(BaseModel):
    client_type: str
    reason: str | None = Field(default=None, max_length=300)


class HoldBody(BaseModel):
    hold: bool
    reason: str = Field(max_length=300)


@router.get("/clients")
async def clients(request: Request, s=Depends(_staff)) -> dict:
    async with use_tenant(s.tenant_id):
        rows = [c async for c in wrap(_store(request)).clients.find({"test": {"$ne": True}}, {"_id": 0, "client_id": 1, "display_name": 1,
                "client_type": 1, "client_type_reason": 1, "legal_hold": 1, "legal_hold_reason": 1})]
    for c in rows:
        c["client_type"] = c.get("client_type") or "retail"
    return {"clients": rows}


@router.put("/clients/{client_id}/type")
async def set_type(client_id: str, body: TypeBody, request: Request, s=Depends(_staff)) -> dict:
    return await _run(cd.set_client_type(_store(request), s.tenant_id, client_id, body.client_type, body.reason, s.user_id))


@router.put("/clients/{client_id}/legal-hold")
async def legal_hold(client_id: str, body: HoldBody, request: Request, s=Depends(_principal)) -> dict:
    await retention.set_hold(_store(request), s.tenant_id, client_id, body.hold, body.reason, s.user_id)
    return {"ok": True}


# ---- complaints & privacy (shared register engine) ----
class NewMatter(BaseModel):
    client_id: str
    category: str
    summary: str = Field(max_length=300)
    details: str | None = Field(default=None, max_length=5000)
    source: str = "call"
    source_ref: str | None = None


class MoveBody(BaseModel):
    to: str
    note: str | None = Field(default=None, max_length=500)
    outcome: str | None = None
    response_text: str | None = Field(default=None, max_length=8000)


def _row(m):
    return {**m, "stage_label": cm.LABEL[m["stage"]], "next": sorted(cm.KIND[m["kind"]]["next"].get(m["stage"], []))}


@router.get("/complaints")
async def complaints(request: Request, s=Depends(_staff)) -> dict:
    rows = await cm.register(_store(request), s.tenant_id, "complaint", s)
    return {"complaints": [_row(m) for m in rows], "stats": cm.idr_stats(rows), "spec": cm.KIND["complaint"] | {"next": None},
            "content_visible_to": "Principal, the assigned AE and whoever logged it"}


@router.post("/complaints")
async def log_complaint(body: NewMatter, request: Request, s=Depends(_staff)) -> dict:
    if body.source not in ("thread", "request", "call", "email", "in_person"):
        raise HTTPException(status_code=422, detail="Source must be thread, request, call, email or in_person.")
    m = await _run(cm.create(_store(request), s.tenant_id, "complaint", **body.model_dump(), by=s.user_id, role=s.active_role))
    return {"complaint": _row(m)}


async def _move(request, s, kind, matter_id, body: MoveBody):
    store = _store(request)
    m = await _run(cm.get(store, s.tenant_id, kind, matter_id))
    if kind == "privacy" and s.active_role != "principal":
        raise HTTPException(status_code=403, detail="Privacy requests are handled by a Principal.")
    if kind == "complaint" and not cm.visible(m, s):
        raise HTTPException(status_code=404, detail="not_found")
    afca = (await cd.settings(store, s.tenant_id))["afca_text"] if kind == "complaint" else None
    return _row(await _run(cm.move(store, s.tenant_id, kind, m, body.to, by=s.user_id, role=s.active_role, note=body.note,
                                   outcome=body.outcome, response_text=body.response_text, afca_text=afca)))


@router.post("/complaints/{matter_id}/move")
async def move_complaint(matter_id: str, body: MoveBody, request: Request, s=Depends(_staff)) -> dict:
    return await _move(request, s, "complaint", matter_id, body)


@router.get("/complaints-register.csv")
async def complaints_csv(request: Request, s=Depends(_lead)) -> PlainTextResponse:
    rows = await cm.register(_store(request), s.tenant_id, "complaint", s)
    buf = io.StringIO()
    w = safe_writer(buf)
    w.writerow(["matter_id", "client", "category", "source", "received", "ack_due", "response_due", "stage", "outcome",
                "response_sent", "closed", "days_open"])
    for m in rows:
        end = m.get("closed_at") or m.get("response_sent_at") or datetime.now(timezone.utc)
        w.writerow([m["matter_id"], m["client_name"], m["category"], m["source"], m["received_at"], m["ack_due"], m["response_due"],
                    m["stage"], m.get("outcome") or "", m.get("response_sent_at") or "", m.get("closed_at") or "",
                    (cm._utc(end) - cm._utc(m["received_at"])).days])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="complaints-register.csv"'})


@router.get("/privacy")
async def privacy(request: Request, s=Depends(_principal)) -> dict:
    rows = await cm.register(_store(request), s.tenant_id, "privacy")
    async with use_tenant(s.tenant_id):
        ex = {e["matter_id"]: e async for e in wrap(_store(request)).privacy_exports.find({}, {"_id": 0, "path": 0})}
    return {"requests": [_row(m) | {"export": ex.get(m["matter_id"])} for m in rows], "stats": cm.idr_stats(rows)}


@router.post("/privacy/{matter_id}/move")
async def move_privacy(matter_id: str, body: MoveBody, request: Request, s=Depends(_principal)) -> dict:
    return await _move(request, s, "privacy", matter_id, body)


# ---- retention / DDO ----
@router.get("/retention")
async def retention_report(request: Request, s=Depends(_lead)) -> dict:
    return await retention.report(_store(request), s.tenant_id)


class RedactBody(BaseModel):
    note: str = Field(max_length=300)


@router.post("/retention/{client_id}/redact")
async def redact(client_id: str, body: RedactBody, request: Request, s=Depends(_principal)) -> dict:
    return await _run(retention.redact(_store(request), s.tenant_id, client_id, body.note, s.user_id))


@router.get("/ddo-reports")
async def ddo_reports(request: Request, s=Depends(_lead)) -> dict:
    async with use_tenant(s.tenant_id):
        return {"reports": [r async for r in wrap(_store(request)).ddo_reports.find({}, {"_id": 0}).sort("at", -1)]}


@router.get("/ai-eval")
async def ai_eval(request: Request, s=Depends(_lead)) -> dict:
    from app.eval import gate
    store = _store(request)
    # tenant-safe: system-only — platform eval history (no tenant data)
    runs = [r async for r in store.eval_runs.find({}, {"_id": 0, "cases": 0}).sort("at", -1).limit(20)]
    latest = await store.eval_runs.find_one({}, {"_id": 0}, sort=[("at", -1)])  # tenant-safe: system-only
    return {"status": await gate.status(store), "runs": runs, "latest_cases": (latest or {}).get("cases") or [],
            "targets": {"safety": gate.SAFETY_TARGET, "overall": gate.OVERALL_TARGET}}


@router.post("/ai-eval/run")
async def ai_eval_run(request: Request, s=Depends(_lead)) -> dict:
    await jobs.enqueue(_store(request), "ai.eval", {"by": s.user_id}, key=f"ai_eval:{datetime.now(timezone.utc):%Y%m%d%H%M}",
                       tenant_id=s.tenant_id)
    return {"queued": True, "note": "Runs the golden set against the live LLM (about a minute; cost recorded)."}


# ---- client ----
def _client(s=Depends(require_session)):
    if s.active_role not in ("client_owner", "client_contact"):
        raise HTTPException(status_code=403, detail="client_role_required")
    return s


async def _mine(request, s) -> set:
    async with use_tenant(s.tenant_id):
        return set(await member_clients(wrap(_store(request)), s.user_id))


@c_router.get("/documents")
async def my_documents(request: Request, s=Depends(_client)) -> dict:
    rows = await cd.deliveries_for(_store(request), s.tenant_id, await _mine(request, s))
    return {"documents": rows}


async def _download(path: str, filename: str, ctype: str) -> Response:
    data, _ = await object_store.get_object(path)
    return Response(data, media_type=ctype, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@c_router.get("/documents/download")
async def download_doc(d: str, request: Request, s=Depends(_client)) -> Response:
    store = _store(request)
    async with use_tenant(s.tenant_id):
        row = await wrap(store).doc_deliveries.find_one({"delivery_id": d})
        doc = row and await wrap(store).disclosure_docs.find_one({"doc_id": row["doc_id"]})
    if not row or row["client_id"] not in await _mine(request, s) or not doc:
        raise HTTPException(status_code=404, detail="not_found")
    return await _download(doc["storage_path"], doc["filename"], doc.get("content_type") or "application/pdf")


class ClientComplaint(BaseModel):
    category: str
    summary: str = Field(max_length=300)
    details: str | None = Field(default=None, max_length=5000)
    client_id: str | None = None


async def _pick(request, s, client_id):
    mine = await _mine(request, s)
    cid = client_id or (sorted(mine)[0] if mine else None)
    if cid not in mine:
        raise HTTPException(status_code=404, detail="not_found")
    return cid


@c_router.get("/complaints")
async def my_complaints(request: Request, s=Depends(_client)) -> dict:
    rows = await cm.register(_store(request), s.tenant_id, "complaint", client_ids=await _mine(request, s))
    return {"complaints": [{k: m.get(k) for k in ("matter_id", "category", "summary", "received_at", "ack_due", "response_due", "stage",
                            "response_text", "outcome")} | {"stage_label": cm.LABEL[m["stage"]]} for m in rows],
            "categories": cm.KIND["complaint"]["categories"], "afca_text": (await cd.settings(_store(request), s.tenant_id))["afca_text"]}


@c_router.post("/complaints")
async def raise_complaint(body: ClientComplaint, request: Request, s=Depends(_client)) -> dict:
    cid = await _pick(request, s, body.client_id)
    m = await _run(cm.create(_store(request), s.tenant_id, "complaint", client_id=cid, category=body.category, summary=body.summary,
                             details=body.details, source="client_app", by=s.user_id, role=s.active_role))
    return {"matter_id": m["matter_id"], "ack_due": m["ack_due"], "response_due": m["response_due"]}


class DataRequest(BaseModel):
    kind: str
    details: str | None = Field(default=None, max_length=3000)
    client_id: str | None = None


@c_router.get("/my-data")
async def my_data(request: Request, s=Depends(_client)) -> dict:
    store = _store(request)
    mine = await _mine(request, s)
    rows = [m for m in await cm.register(store, s.tenant_id, "privacy", client_ids=mine) if m.get("raised_by") == s.user_id]
    now = datetime.now(timezone.utc)
    async with use_tenant(s.tenant_id):
        ex = [e async for e in wrap(store).privacy_exports.find({"user_id": s.user_id}, {"_id": 0, "path": 0}).sort("created_at", -1)]
    st = await cd.settings(store, s.tenant_id)
    return {"requests": [{k: m.get(k) for k in ("matter_id", "category", "received_at", "response_due", "stage", "outcome")}
                         | {"stage_label": cm.LABEL[m["stage"]]} for m in rows],
            "exports": [e | {"expired": cm._utc(e["expires_at"]) <= now} for e in ex],
            "privacy_policy_text": st["privacy_policy_text"], "collection_notice_text": st["collection_notice_text"]}


@c_router.post("/my-data/requests")
async def data_request(body: DataRequest, request: Request, s=Depends(_client)) -> dict:
    if body.kind not in ("access", "correction", "complaint"):
        raise HTTPException(status_code=422, detail="Choose access, correction or complaint.")
    if body.kind != "access" and not (body.details or "").strip():
        raise HTTPException(status_code=422, detail="Tell us what needs correcting or what went wrong.")
    store, cid = _store(request), await _pick(request, s, body.client_id)
    m = await _run(cm.create(store, s.tenant_id, "privacy", client_id=cid, category=body.kind,
                             summary={"access": "Access to my data", "correction": "Correct my data", "complaint": "Privacy complaint"}[body.kind],
                             details=body.details, source="client_app", by=s.user_id, role=s.active_role))
    if body.kind == "access":
        await jobs.enqueue(store, "privacy.export", {"matter_id": m["matter_id"], "client_id": cid, "user_id": s.user_id},
                           key=f"privacy_export:{m['matter_id']}", tenant_id=s.tenant_id)
    return {"matter_id": m["matter_id"], "response_due": m["response_due"], "export": "queued" if body.kind == "access" else None}


@c_router.get("/my-data/export")
async def download_export(e: str, fmt: str, request: Request, s=Depends(_client)) -> Response:
    async with use_tenant(s.tenant_id):
        row = await wrap(_store(request)).privacy_exports.find_one({"export_id": e, "user_id": s.user_id})
    if not row or fmt not in ("json", "pdf"):
        raise HTTPException(status_code=404, detail="not_found")
    if cm._utc(row["expires_at"]) <= datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="This download link has expired. Ask for a new copy.")
    return await _download(f"{row['path']}.{fmt}", f"my-data.{fmt}", "application/json" if fmt == "json" else "application/pdf")
