"""R3.13 — staff upload real schedules and PDS/wording per policy (AE/Principal; Operator read-only). PDF only, R3.6 PDF checks,
20MB. Stored in Object Storage via policy_documents; replace/withdraw hides from the client and is ledgered, never deleted."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from app.db.tenant_safe import wrap
from app.kernel import media, object_store
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch
from app.middleware.session import require_operator_session

router = APIRouter(prefix="/api/o", tags=["policy-documents"])
WRITERS = ("ae", "principal")
KINDS = {"schedule": "Policy schedule", "pds": "Product Disclosure Statement", "wording": "Policy wording"}


class Withdraw(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=300)


def _writer(s) -> None:
    if s.active_role not in WRITERS:
        raise HTTPException(status_code=403, detail="Only an account executive or Principal can change client documents.")


def _view(d: dict) -> dict:
    return {"doc_id": d["doc_id"], "policy_id": d["policy_id"], "kind": d["kind"], "kind_label": KINDS.get(d["kind"], d["kind"]),
            "title": d["title"], "term_label": d.get("term_label") or "", "current": bool(d.get("current")),
            "sample": bool(d.get("sample")), "filename": d["filename"], "created_at": d["created_at"].isoformat() if d.get("created_at") else None,
            "withdrawn": bool(d.get("withdrawn_at")), "withdrawn_reason": d.get("withdrawn_reason")}


async def _ledger(store, s, typ, payload, idem):
    await append_via_dispatch(store, s.tenant_id, typ, actor={"kind": "human", "id": s.user_id, "role": s.active_role},
                              payload=payload, idempotency_key=idem)


@router.get("/clients/{client_id}/policy-documents")
async def list_docs(client_id: str, request: Request, s=Depends(require_operator_session)) -> dict:
    safe = wrap(request.app.state.mongo_db)
    async with use_tenant(s.tenant_id):
        pols = [p async for p in safe.policies.find({"client_id": client_id, "test": {"$ne": True}},
                                                     {"_id": 0, "policy_id": 1, "product": 1, "status": 1, "insurer_display": 1})]
        docs = [d async for d in safe.policy_documents.find({"client_id": client_id}, {"_id": 0}).sort("created_at", -1)]
    return {"can_edit": s.active_role in WRITERS, "kinds": KINDS,
            "policies": [{**p, "documents": [_view(d) for d in docs if d["policy_id"] == p["policy_id"]]} for p in pols
                         if p.get("status") in ("managed", "in_force", "lapsed", "renewed")]}


@router.post("/policies/{policy_id}/documents")
async def upload(policy_id: str, request: Request, file: UploadFile = File(...), kind: str = Form(...), term_label: str = Form(""),
                 current: bool = Form(True), replaces: str | None = Form(None), s=Depends(require_operator_session)) -> dict:
    _writer(s)
    if kind not in KINDS:
        raise HTTPException(status_code=422, detail="Choose a schedule, PDS or wording.")
    store = request.app.state.mongo_db
    safe = wrap(store)
    async with use_tenant(s.tenant_id):
        p = await safe.policies.find_one({"policy_id": policy_id}, {"_id": 0})
        old = await safe.policy_documents.find_one({"doc_id": replaces, "policy_id": policy_id}) if replaces else None
    if not p:
        raise HTTPException(status_code=404, detail="not_found")
    if replaces and not old:
        raise HTTPException(status_code=404, detail="The document you're replacing wasn't found.")
    data = await file.read(media.MAX_PDF + 1)
    if len(data) > media.MAX_PDF:
        raise HTTPException(status_code=413, detail="That PDF is over 20MB.")
    if (file.content_type or "").split(";")[0] != media.PDF_TYPE:
        raise HTTPException(status_code=415, detail="PDF files only.")
    try:
        media.check_pdf(data)
    except media.Rejected as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    doc_id, now = str(uuid.uuid4()), datetime.now(timezone.utc)
    path = f"{object_store.APP_NAME}/tenants/{s.tenant_id}/policy-docs/{policy_id}/{doc_id}.pdf"
    await object_store.put_object(path, data, media.PDF_TYPE)
    sha = hashlib.sha256(data).hexdigest()
    is_current = bool(current) if kind == "schedule" else True
    row = {"doc_id": doc_id, "client_id": p["client_id"], "policy_id": policy_id, "kind": kind, "current": is_current,
           "title": KINDS[kind], "term_label": term_label.strip()[:80], "filename": (file.filename or f"{kind}.pdf")[:120],
           "storage_path": path, "sha256": sha, "size": len(data), "uploaded_by": s.user_id, "created_at": now, "withdrawn_at": None,
           "demoted": []}
    async with use_tenant(s.tenant_id):
        demoted = []
        if kind == "schedule" and is_current:
            demoted = [d["doc_id"] async for d in safe.policy_documents.find({"policy_id": policy_id, "kind": "schedule", "current": True,
                                                                               "withdrawn_at": None})]
            await safe.policy_documents.update_many({"doc_id": {"$in": demoted}}, {"$set": {"current": False}})
        row["demoted"] = [x for x in demoted if not old or x != old["doc_id"]]
        await safe.policy_documents.insert_one(dict(row))
        if old:
            await safe.policy_documents.update_one({"doc_id": old["doc_id"]}, {"$set": {"withdrawn_at": now, "withdrawn_by": s.user_id,
                                                                                       "withdrawn_reason": "Replaced", "replaced_by": doc_id}})
    await _ledger(store, s, "PolicyDocumentAdded", {"doc_id": doc_id, "policy_id": policy_id, "client_id": p["client_id"], "kind": kind,
                  "term_label": row["term_label"], "current": is_current, "sha256": sha, "size": len(data), "now_previous": demoted,
                  "replaces": replaces}, f"policy_doc_added:{doc_id}")
    if old:
        await _ledger(store, s, "PolicyDocumentReplaced", {"doc_id": old["doc_id"], "replaced_by": doc_id, "policy_id": policy_id,
                      "client_id": p["client_id"], "kind": kind}, f"policy_doc_replaced:{old['doc_id']}")
    return {"ok": True, "document": _view(row)}


@router.post("/policy-documents/{doc_id}/withdraw")
async def withdraw(doc_id: str, body: Withdraw, request: Request, s=Depends(require_operator_session)) -> dict:
    _writer(s)
    store = request.app.state.mongo_db
    async with use_tenant(s.tenant_id):
        d = await wrap(store).policy_documents.find_one({"doc_id": doc_id})
        if not d:
            raise HTTPException(status_code=404, detail="not_found")
        if d.get("withdrawn_at"):
            raise HTTPException(status_code=409, detail="Already withdrawn.")
        await wrap(store).policy_documents.update_one({"doc_id": doc_id}, {"$set": {
            "withdrawn_at": datetime.now(timezone.utc), "withdrawn_by": s.user_id, "withdrawn_reason": body.reason.strip(), "current": False}})
        restored = []
        if d.get("current") and d.get("demoted"):  # put back the schedule this one had pushed to "previous"
            restored = [x["doc_id"] async for x in wrap(store).policy_documents.find(
                {"doc_id": {"$in": d["demoted"]}, "withdrawn_at": None})]
            await wrap(store).policy_documents.update_many({"doc_id": {"$in": restored}}, {"$set": {"current": True}})
    await _ledger(store, s, "PolicyDocumentWithdrawn", {"doc_id": doc_id, "policy_id": d["policy_id"], "client_id": d["client_id"],
                  "kind": d["kind"], "reason": body.reason.strip(), "restored_current": restored}, f"policy_doc_withdrawn:{doc_id}")
    return {"ok": True}


@router.get("/policy-documents/{doc_id}/file")
async def staff_file(doc_id: str, request: Request, s=Depends(require_operator_session)) -> Response:
    async with use_tenant(s.tenant_id):
        d = await wrap(request.app.state.mongo_db).policy_documents.find_one({"doc_id": doc_id})
    if not d:
        raise HTTPException(status_code=404, detail="not_found")
    data, _ = await object_store.get_object(d["storage_path"])
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{d["filename"]}"'})
