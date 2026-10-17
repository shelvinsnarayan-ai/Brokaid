"""R1.4 — /api/o/import (Operator + Principal, own tenant) and /api/o/clean-start (Operator)."""
from __future__ import annotations

import csv
import hashlib
import io
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from app.db.tenant_safe import wrap
from app.kernel import iadviser as ia
from app.kernel import jobs, object_store, tenant_archive
from app.kernel.tenant_ctx import use_tenant
from app.middleware.session import require_operator_session

router = APIRouter(prefix="/api/o", tags=["import"])
MIME = {"csv": "text/csv", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


def _store(request: Request):
    return request.app.state.mongo_db


async def importer(s=Depends(require_operator_session)):
    if s.active_role not in ("operator", "principal"):
        raise HTTPException(status_code=403, detail="Only an Operator or Principal can import data.")
    return s


async def _profile(safe, profile_id: str | None) -> dict | None:
    q = {"profile_id": profile_id} if profile_id else {}
    rows = [p async for p in safe.import_profiles.find(q, {"_id": 0}).sort("updated_at", -1).limit(1)]
    return rows[0] if rows else None


@router.get("/import/fields")
async def fields(s=Depends(importer)) -> dict:
    return {"entities": {e: [{"field": f, "required": r} for f, (r, _) in fs.items()] for e, fs in ia.FIELDS.items()}}


@router.post("/import/files")
async def upload(request: Request, file: UploadFile = File(...), profile_id: str | None = None, s=Depends(importer)) -> dict:
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in MIME:
        raise HTTPException(status_code=422, detail="Upload a .csv or .xlsx export.")
    data = await file.read()
    if len(data) > ia.MAX_BYTES:
        raise HTTPException(status_code=413, detail="That file is over 20 MB.")
    try:
        sheets = ia.parse(data, file.filename)
    except Exception:  # noqa: BLE001 — never echo file contents
        raise HTTPException(status_code=422, detail="We couldn't read that file. Export it again as CSV or XLSX.") from None
    if not sheets:
        raise HTTPException(status_code=422, detail="That file has no rows.")
    sha = hashlib.sha256(data).hexdigest()
    path = f"{object_store.APP_NAME}/imports/{s.tenant_id}/{uuid.uuid4()}.{ext}"
    stored = await object_store.put_object(path, data, MIME[ext])
    safe = wrap(_store(request))
    async with use_tenant(s.tenant_id):
        prof = await _profile(safe, profile_id)
        out = []
        for sh in sheets:
            ent = ia.detect_entity(sh["name"], sh["headers"])
            out.append({"name": sh["name"], "headers": sh["headers"], "row_count": len(sh["rows"]), "entity": ent,
                        "mapping": ia.suggest(sh["headers"], ent, (prof or {}).get("entities")),
                        "suggested": {e: ia.suggest(sh["headers"], e, (prof or {}).get("entities")) for e in ia.ORDER},
                        "sample": [[ia._txt(c) if c is not None else "" for c in r] for r in sh["rows"][:3]]})
        doc = {"file_id": str(uuid.uuid4()), "filename": file.filename, "sha256": sha, "size": len(data),
               "storage_path": stored.get("path", path), "content_type": MIME[ext], "uploaded_by": s.user_id,
               "at": datetime.now(timezone.utc), "is_deleted": False,
               "sheets": [{k: o[k] for k in ("name", "headers", "row_count", "entity")} for o in out]}
        await safe.import_files.insert_one(dict(doc))
    return {"file_id": doc["file_id"], "filename": file.filename, "sha256": sha, "size": len(data), "sheets": out,
            "profile": prof and {"profile_id": prof["profile_id"], "name": prof["name"]}}


class SheetPlan(BaseModel):
    name: str
    entity: str
    mapping: dict[str, str | None]


class RunBody(BaseModel):
    file_id: str
    sheets: list[SheetPlan]
    save_profile: str | None = None


@router.post("/import/runs")
async def create_run(body: RunBody, request: Request, s=Depends(importer)) -> dict:
    store = _store(request)
    safe = wrap(store)
    async with use_tenant(s.tenant_id):
        f = await safe.import_files.find_one({"file_id": body.file_id, "is_deleted": False})
    if not f:
        raise HTTPException(status_code=404, detail="not_found")
    for sp in body.sheets:
        if sp.entity not in ia.FIELDS:
            raise HTTPException(status_code=422, detail=f"Unknown data type {sp.entity}.")
        missing = [k for k, (req, _) in ia.FIELDS[sp.entity].items() if req and not sp.mapping.get(k)]
        if missing:
            raise HTTPException(status_code=422, detail=f"{sp.name}: map the required column(s) {', '.join(missing)}.")
    data, _ = await object_store.get_object(f["storage_path"])
    if hashlib.sha256(data).hexdigest() != f["sha256"]:
        raise HTTPException(status_code=409, detail="The stored file doesn't match its fingerprint.")
    parsed = {sh["name"]: sh for sh in ia.parse(data, f["filename"])}
    plans = [{**parsed[sp.name], "entity": sp.entity, "mapping": sp.mapping} for sp in body.sheets if sp.name in parsed]
    run_id = str(uuid.uuid4())
    counts = await ia.validate(store, s.tenant_id, run_id=run_id, file_sha=f["sha256"], sheets=plans)
    async with use_tenant(s.tenant_id):
        me = await safe.users.find_one({"user_id": s.user_id}) or {}
        run = {"run_id": run_id, "file_id": f["file_id"], "filename": f["filename"], "file_sha": f["sha256"],
               "status": "validated", "counts": counts, "created_by": s.user_id,
               "created_by_name": me.get("name") or me.get("display_name") or me.get("email"),
               "created_at": datetime.now(timezone.utc), "mapping": [sp.model_dump() for sp in body.sheets],
               "progress": {"done": 0, "total": sum(c["create"] + c["update"] for c in counts.values())}}
        await safe.import_runs.insert_one(dict(run))
        if body.save_profile:
            ents = {sp.entity: sp.mapping for sp in body.sheets}
            await safe.import_profiles.update_one({"name": body.save_profile.strip()}, {
                "$set": {"entities": ents, "updated_at": datetime.now(timezone.utc), "updated_by": s.user_id},
                "$setOnInsert": {"profile_id": str(uuid.uuid4())}}, upsert=True)
    run.pop("_id", None)
    return run


@router.get("/import/runs")
async def runs(request: Request, s=Depends(importer)) -> dict:
    safe = wrap(_store(request))
    async with use_tenant(s.tenant_id):
        return {"runs": [r async for r in safe.import_runs.find({}, {"_id": 0, "mapping": 0}).sort("created_at", -1).limit(50)]}


async def _run(safe, run_id: str) -> dict:
    r = await safe.import_runs.find_one({"run_id": run_id}, {"_id": 0})
    if not r:
        raise HTTPException(status_code=404, detail="not_found")
    return r


@router.get("/import/runs/{run_id}")
async def get_run(run_id: str, request: Request, s=Depends(importer)) -> dict:
    safe = wrap(_store(request))
    async with use_tenant(s.tenant_id):
        return await _run(safe, run_id)


@router.get("/import/runs/{run_id}/rows")
async def rows(run_id: str, request: Request, status: str | None = None, action: str | None = None,
               entity: str | None = None, skip: int = 0, limit: int = 100, s=Depends(importer)) -> dict:
    safe = wrap(_store(request))
    q = {"run_id": run_id, **{k: v for k, v in (("status", status), ("action", action), ("entity", entity)) if v}}
    async with use_tenant(s.tenant_id):
        await _run(safe, run_id)
        total = await safe.import_rows.count_documents(q)
        cur = safe.import_rows.find(q, {"_id": 0}).sort([("order", 1), ("row", 1)]).skip(max(0, skip)).limit(min(500, limit))
        return {"total": total, "rows": [r async for r in cur]}


@router.get("/import/runs/{run_id}/errors.csv")
async def errors_csv(run_id: str, request: Request, s=Depends(importer)) -> Response:
    safe = wrap(_store(request))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["sheet", "row", "data type", "key", "result", "reasons"])
    async with use_tenant(s.tenant_id):
        await _run(safe, run_id)
        async for r in safe.import_rows.find({"run_id": run_id, "status": {"$ne": "ok"}}).sort([("order", 1), ("row", 1)]):
            w.writerow([r["sheet"], r["row"], r["entity"], r["key"] or "", r["status"], "; ".join(i["text"] for i in r["issues"])])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="import-{run_id[:8]}-issues.csv"'})


@router.post("/import/runs/{run_id}/commit")
async def commit(run_id: str, request: Request, s=Depends(importer)) -> dict:
    store = _store(request)
    safe = wrap(store)
    async with use_tenant(s.tenant_id):
        run = await _run(safe, run_id)
        if run["status"] not in ("validated", "committing"):
            raise HTTPException(status_code=409, detail="This import was already committed.")
        newer = await safe.import_runs.find_one({"status": {"$in": ["committing", "committed"]},
                                                 "created_at": {"$gt": run["created_at"]}})
        if newer:
            raise HTTPException(status_code=409, detail="A newer import has been committed. Check the file again first.")
        await safe.import_runs.update_one({"run_id": run_id}, {"$set": {"status": "committing", "commit_by": s.user_id}})
    job = await jobs.enqueue(store, "import.commit", {"run_id": run_id}, key=f"import:{run_id}", tenant_id=s.tenant_id)
    return {"run_id": run_id, "status": "committing", "job_id": job["job_id"]}


@router.get("/import/runs/{run_id}/reconciliation")
async def reconciliation(run_id: str, request: Request, s=Depends(importer)) -> dict:
    safe = wrap(_store(request))
    async with use_tenant(s.tenant_id):
        run = await _run(safe, run_id)
        keys = {e: set() for e in ia.ORDER}
        async for r in safe.import_rows.find({"run_id": run_id}, {"entity": 1, "key": 1}):
            keys[r["entity"]].add(r["key"])
        missing = {}
        for ent, coll, fld, label in (("clients", "clients", "external.iadviser_client_code", "display_name"),
                                      ("policies", "policies", "external.iadviser_policy_number", "product")):
            missing[ent] = [{"key": d["external"][fld.split(".")[1]], "name": d.get(label)}
                            async for d in safe[coll].find({fld: {"$exists": True}, "archived_at": None})
                            if d["external"][fld.split(".")[1]] not in keys[ent]][:200]
        return {"run_id": run_id, "counts": run["counts"], "not_in_file": missing}


@router.get("/import/profiles")
async def profiles(request: Request, s=Depends(importer)) -> dict:
    safe = wrap(_store(request))
    async with use_tenant(s.tenant_id):
        return {"profiles": [p async for p in safe.import_profiles.find({}, {"_id": 0}).sort("updated_at", -1)]}


async def operator_only(s=Depends(require_operator_session)):
    if s.active_role != "operator":
        raise HTTPException(status_code=403, detail="Only the Operator can run a clean start.")
    return s


@router.get("/clean-start/dry-run")
async def clean_dry_run(request: Request, s=Depends(operator_only)) -> dict:
    return await tenant_archive.dry_run(_store(request), s.tenant_id, s.user_id)


class CleanBody(BaseModel):
    dry_run_id: str
    confirm: str


@router.post("/clean-start")
async def clean_start(body: CleanBody, request: Request, s=Depends(operator_only)) -> dict:
    try:
        return await tenant_archive.execute(_store(request), s.tenant_id, s.user_id, s.active_role, body.dry_run_id, body.confirm)
    except tenant_archive.Refused as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
