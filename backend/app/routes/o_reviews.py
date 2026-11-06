"""R3.11: /api/o/reviews. Read-only list, summary, and downloads (PDF, Markdown, raw JSON per model). Operator and Principal only."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.auth.sessions import Session
from app.db.tenant_safe import wrap
from app.kernel import object_store
from app.kernel.tenant_ctx import use_tenant
from app.middleware.session import require_session

router = APIRouter(prefix="/api/o/reviews")


def _reader(s: Session = Depends(require_session)) -> Session:
    if s.active_role not in ("operator", "principal"):
        raise HTTPException(status_code=403, detail={"code": "AuthorityDenied", "detail": "Reviews are for the Operator and Principal."})
    return s


async def _get(request: Request, s: Session, review_id: str) -> dict:
    async with use_tenant(s.tenant_id):
        d = await wrap(request.app.state.mongo_db).reviews.find_one({"review_id": review_id}, {"_id": 0})
    if not d:
        raise HTTPException(status_code=404, detail="review_not_found")
    return d


@router.get("")
async def list_reviews(request: Request, s: Session = Depends(_reader)) -> dict:
    async with use_tenant(s.tenant_id):
        rows = [d async for d in wrap(request.app.state.mongo_db).reviews.find({}, {"_id": 0, "review_id": 1, "title": 1, "created_at": 1, "sha": 1,
                                                                                    "models": 1, "consolidated.surfaces": 1}).sort("created_at", -1)]
    return {"reviews": [{**r, "created_at": r["created_at"].isoformat()} for r in rows]}


@router.get("/{review_id}")
async def get_review(review_id: str, request: Request, s: Session = Depends(_reader)) -> dict:
    d = await _get(request, s, review_id)
    return {"review": {**{k: d[k] for k in ("review_id", "title", "sha", "models", "consolidated", "tasks_measured")}, "compare": d.get("compare"),
                       "created_at": d["created_at"].isoformat(), "raw_models": list(d["raw"]), "evidence_ids": list(d["evidence"])}}


@router.get("/{review_id}/download/{kind}")
async def download(review_id: str, kind: str, request: Request, s: Session = Depends(_reader)) -> Response:
    """kind: pdf | md | raw-<model_id> | evidence-<screenshot id>"""
    d = await _get(request, s, review_id)
    if kind in d["files"]:
        path, ctype, name = d["files"][kind], "application/pdf" if kind == "pdf" else "text/markdown", d["files"][kind].rsplit("/", 1)[1]
    elif kind.startswith("raw-") and kind[4:] in d["raw"]:
        path, ctype, name = d["raw"][kind[4:]], "application/json", f"{review_id}-{kind[4:]}.json"
    elif kind.startswith("evidence-") and kind[9:] in d["evidence"]:
        path, ctype, name = d["evidence"][kind[9:]], "image/png", f"{kind[9:]}.png"
    else:
        raise HTTPException(status_code=404, detail="file_not_found")
    data, _ = await object_store.get_object(path)
    disp = "inline" if ctype == "image/png" else "attachment"
    return Response(data, media_type=ctype, headers={"Content-Disposition": f'{disp}; filename="{name}"', "Cache-Control": "private, no-store"})
