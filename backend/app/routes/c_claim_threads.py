"""R3.4 — client claim threads: GET /api/c/claim-threads, GET|POST /api/c/claims/{claim_id}/thread (text only; no AI auto-reply)."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.auth.sessions import Session
from app.db.tenant_safe import wrap
from app.kernel import authority, filetype, inbox, media, object_store
from app.kernel import claim_threads as ct
from app.kernel import claims as ck
from app.kernel.tenant_ctx import use_tenant
from app.middleware.session import require_session
from app.routes.c_claims import _client, _mine, _store

router = APIRouter(prefix="/api/c", tags=["client-claim-threads"])
UUID_RE = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


class PostBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(default="", max_length=inbox.MAX_BODY)
    file_ids: list[str] = Field(default_factory=list, max_length=media.MAX_FILES)
    msg_id: str | None = Field(default=None, pattern=UUID_RE)

    @field_validator("message")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @model_validator(mode="after")
    def _not_blank(self):
        if not self.message and not self.file_ids:
            raise ValueError("message cannot be empty")
        if self.file_ids and not self.msg_id:
            raise ValueError("msg_id is required with attachments")
        return self


def _err(status: int, code: str, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "detail": detail})


async def _postable(store, session, claim_id: str) -> dict:
    c = await _claim(store, session, claim_id)
    if ct.is_closed(c):
        raise _err(409, "claim_closed", ct.CLOSED_NOTE)
    await authority.require_authority(store, session, client_id=c["client_id"], action="claim_message", needed="can_lodge_claim")
    return c


async def _seen(store, session) -> dict:
    async with use_tenant(session.tenant_id):
        return {s["thread_id"]: inbox._utc(s["last_seen_at"]) async for s in wrap(store).ask_seen.find({"user_id": session.user_id})}


def _unread(t: dict | None, since) -> list[str]:
    return [m["reply_id"] for m in (t or {}).get("messages", []) if m.get("reply_id") and (since is None or m["at"] > since)]


async def _claim(store, session, claim_id: str) -> dict:
    c = await _mine(store, session, claim_id)  # central ownership guard: another client's claim is a 404
    if c.get("test") or "stage" not in c:
        raise HTTPException(status_code=404, detail="claim_not_found")
    return c


@router.get("/claim-threads")
async def list_threads(request: Request, session: Session = Depends(require_session)) -> dict[str, Any]:
    store = _store(request)
    cid = await _client(store, session)
    now = datetime.now(timezone.utc)
    async with use_tenant(session.tenant_id):
        claims = [c async for c in wrap(store).claims.find({"client_id": cid, "test": {"$ne": True}, "stage": {"$exists": True}}, {"_id": 0})]
    threads = {t["claim_id"]: t for t in await inbox.load_threads(store, session.tenant_id, client_id=cid) if t.get("claim_id")}
    seen, names = await _seen(store, session), await ct.vehicle_names(store, session.tenant_id, cid)
    rows = []
    for c in claims:
        t, closed = threads.get(c["claim_id"]), ct.is_closed(c)
        if closed and not t:
            continue
        rows.append({"claim_id": c["claim_id"], "claim_reference": c.get("claim_reference"), "label": ct.label(c, names),
                     "stage_label": ck.row(c, now)["stage_label"], "open": not closed, "thread_id": t["thread_id"] if t else None,
                     "last_message": t["last_message"][:120] if t else None, "last_at": t["last_at"].isoformat() if t else None,
                     "unread": len(_unread(t, seen.get(ct.thread_id_for(c["claim_id"])))),
                     "sort": (t["last_at"] if t else inbox._utc(c.get("lodged_at") or now)).isoformat()})
    rows.sort(key=lambda r: r.pop("sort"), reverse=True)
    rows.sort(key=lambda r: not r["open"])
    return {"threads": rows}


@router.get("/claims/{claim_id}/thread")
async def get_thread(claim_id: str, request: Request, session: Session = Depends(require_session)) -> dict[str, Any]:
    store = _store(request)
    c = await _claim(store, session, claim_id)
    t = await ct.thread_for(store, session.tenant_id, c["client_id"], claim_id)
    since = (await _seen(store, session)).get(ct.thread_id_for(claim_id))
    msgs = [{"who": m["who"], "text": m["text"], "at": m["at"].isoformat(), "author": m.get("author"), "reply_id": m.get("reply_id"),
             "gaw": m.get("gaw"), "attachments": ct.attachments(c, m.get("file_ids") or [])}
            for m in (t or {}).get("messages", []) if m["who"] in ("client", "broker")]
    closed = ct.is_closed(c)
    waiting = t and not t["answered"] and t["reply_by"]
    return {"claim": {"claim_id": claim_id, "claim_reference": c.get("claim_reference"),
                      "label": ct.label(c, await ct.vehicle_names(store, session.tenant_id, c["client_id"])),
                      "stage_label": ck.row(c, datetime.now(timezone.utc))["stage_label"]},
            "thread_id": ct.thread_id_for(claim_id), "exists": bool(t), "messages": msgs, "unread_reply_ids": _unread(t, since),
            "read_only": closed, "closed_note": ct.CLOSED_NOTE if closed else None,
            "can_post": not closed and await authority.has(store, session, c["client_id"], "can_lodge_claim"),
            "reply_by_text": f"Your broker will reply by {inbox.fmt_long(t['reply_by'])}." if waiting else None,
            "attachment_limits": {"max_files": media.MAX_FILES, "photo_bytes": media.MAX_PHOTO, "video_bytes": media.MAX_VIDEO,
                                  "message_bytes": media.MAX_MESSAGE, "videos_enabled": bool(media.ffmpeg_exe())}}


@router.post("/claims/{claim_id}/thread")
async def post_message(claim_id: str, request: Request, body: PostBody = Body(...),
                       session: Session = Depends(require_session)) -> dict[str, Any]:
    store = _store(request)
    c = await _postable(store, session, claim_id)
    if body.msg_id and await ct.message_sent(store, session.tenant_id, claim_id, body.msg_id):
        return {"thread_id": ct.thread_id_for(claim_id), "msg_id": body.msg_id, "replayed": True}
    docs = []
    if body.file_ids:
        try:
            docs = await ct.attach(store, session.tenant_id, c, file_ids=body.file_ids, msg_id=body.msg_id, user_id=session.user_id,
                                   role=session.active_role)
        except ck.Bad as e:
            raise _err(400, "attachment_invalid", str(e)) from e
    return await ct.post_client(store, session.tenant_id, user_id=session.user_id, role=session.active_role, claim=c, message=body.message,
                                msg_id=body.msg_id, file_ids=[d["file_id"] for d in docs], attachment_summary=ct.summary(docs))


_UNSUPPORTED = {"client": "That file type can't be attached. Send a photo (JPEG, PNG, WebP, HEIC) or a video (MP4, MOV).",
                "staff": "That file type can't be attached. Attach a photo (JPEG, PNG, WebP, HEIC) or a PDF."}
_TOO_LARGE = {"video": "Videos can be up to 100 MB.", "photo": "Photos can be up to 10 MB.", "document": "PDFs can be up to 20 MB."}


async def _receive(file: UploadFile, tmp, allowed: tuple[str, ...], who: str) -> tuple[str, int]:
    """Stream the upload to a temp file; type comes from the first bytes, size is capped as it arrives."""
    ctype, size = None, 0
    while chunk := await file.read(1024 * 1024):
        if ctype is None:
            ctype = filetype.sniff(chunk[:32])
            if ctype not in allowed:
                raise _err(415, "unsupported_type", _UNSUPPORTED[who])
            if ctype in media.VIDEO_TYPES and not media.ffmpeg_exe():
                raise _err(503, "video_unavailable", "Video uploads are switched off right now. Send photos, or try again later.")
        size += len(chunk)
        if size > media.limit_for(ctype):
            raise _err(413, "too_large", _TOO_LARGE[media.kind_of(ctype)])
        tmp.write(chunk)
    if not size:
        raise _err(400, "empty_file", "That file is empty.")
    tmp.flush()
    return ctype, size


async def stage_upload(store, session, c: dict, file: UploadFile, msg_id: str, *, sent: bool, allowed: tuple[str, ...],
                       who: str, label: str | None = None) -> dict:
    """R3.5/R3.6 shared: limits, streamed receive, strip/check + store + stage; temp file removed on every path."""
    if sent:
        raise _err(409, "already_sent", "This message was already sent. Start a new one.")
    rows = await ct.staged(store, session.tenant_id, c["claim_id"], msg_id, session.user_id)
    if len(rows) >= media.MAX_FILES:
        raise _err(400, "too_many_files", "You can attach up to 5 files to one message.")
    tmp = tempfile.NamedTemporaryFile(prefix="r35-in-", delete=False)
    try:
        ctype, size = await _receive(file, tmp, allowed, who)
        tmp.close()
        if size + sum(r["doc"]["size"] for r in rows) > media.MAX_MESSAGE:
            raise _err(413, "too_large", "Attachments on one message can add up to 250 MB.")
        doc = await ct.stage(store, session.tenant_id, c, src=tmp.name, content_type=ctype, filename=file.filename or "",
                             user_id=session.user_id, msg_id=msg_id, role="client" if who == "client" else session.active_role,
                             label=label)
    except media.Rejected as e:
        raise _err({"unsupported_type": 415, "video_unavailable": 503}.get(e.code, 422), e.code, str(e)) from e
    finally:
        tmp.close()
        os.unlink(tmp.name)
    return {"file": ck._doc_out(doc)}


@router.post("/claims/{claim_id}/thread/attachments")
async def upload_attachment(claim_id: str, request: Request, file: UploadFile = File(...), msg_id: str = Form(..., pattern=UUID_RE),
                            session: Session = Depends(require_session)) -> dict[str, Any]:
    """R3.5: one photo/video for a draft claim message (magic bytes checked, metadata stripped, stored privately, staged)."""
    store = _store(request)
    c = await _postable(store, session, claim_id)
    sent = await ct.message_sent(store, session.tenant_id, claim_id, msg_id)
    return await stage_upload(store, session, c, file, msg_id, sent=sent, allowed=media.CLIENT_TYPES, who="client")


@router.delete("/claims/{claim_id}/thread/attachments/{file_id}")
async def discard_attachment(claim_id: str, file_id: str, request: Request, session: Session = Depends(require_session)) -> dict:
    store = _store(request)
    await _claim(store, session, claim_id)
    if not await ct.discard(store, session.tenant_id, claim_id, file_id, session.user_id):
        raise HTTPException(status_code=404, detail="file_not_found")
    return {"ok": True}


@router.get("/claims/{claim_id}/thread/attachments/{file_id}/preview")
async def staged_preview(claim_id: str, file_id: str, request: Request, session: Session = Depends(require_session)) -> Response:
    """The JPEG preview of the client's own not-yet-sent HEIC/video (browsers can't show HEIC)."""
    store = _store(request)
    await _claim(store, session, claim_id)
    async with use_tenant(session.tenant_id):
        r = await wrap(store).claim_uploads.find_one({"file_id": file_id, "claim_id": claim_id, "user_id": session.user_id, "status": "staged"})
    if not r or not r["doc"].get("preview_path"):
        raise HTTPException(status_code=404, detail="file_not_found")
    data, _ = await object_store.get_object(r["doc"]["preview_path"])
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=300"})
