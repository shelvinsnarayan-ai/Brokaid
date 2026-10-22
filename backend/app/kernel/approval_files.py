"""R3.7 — files held with a personal-advice approval. The approval covers the exact text plus the exact file set (sha256 + label
per file, hashed together). Until a Principal approves, the files stay staged drafts that only staff can see. On approval they're
re-verified (bytes re-hashed from storage) and go out with the text as one reply. Any mismatch refuses, and nothing is sent."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import HTTPException

from app.db.tenant_safe import wrap
from app.kernel import claim_threads as ct
from app.kernel import inbox, media, object_store
from app.kernel.tenant_ctx import use_tenant

CHANGED = "The attached files changed after approval was requested, so nothing was sent. Re-attach them and ask for a fresh approval."


def files_hash(files: list[dict]) -> str:
    return hashlib.sha256("\n".join(sorted(f"{f['sha256']}:{f.get('label') or ''}" for f in files)).encode()).hexdigest()


def _err(status: int, code: str, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "detail": detail})


async def claim_for(store, tid: str, thread_id: str) -> dict:
    async with use_tenant(tid):
        c = await wrap(store).claims.find_one({"claim_id": thread_id[len(ct.PREFIX):]}, {"_id": 0})
    if not c:
        raise HTTPException(status_code=404, detail="thread_not_found")
    return c


async def bind(store, session, c: dict, *, draft_id: str, file_ids: list[str], labels: dict) -> list[dict]:
    """The author's own staged, not-yet-held drafts → the file refs the approval covers (label fixed now)."""
    rows = {r["file_id"]: r["doc"] for r in await ct.staged(store, session.tenant_id, c["claim_id"], draft_id, session.user_id)
            if not r.get("held_by")}
    if len(set(file_ids)) != len(file_ids) or any(f not in rows for f in file_ids):
        raise _err(400, "attachment_invalid", "One of the attachments is missing or was removed. Remove it and attach it again.")
    out = []
    for f in file_ids:
        d = rows[f]
        out.append({"file_id": f, "sha256": d["sha256"], "label": labels.get(f) if labels.get(f) in media.LABELS else d.get("label"),
                    "size": d["size"], "kind": d["kind"], "filename": d["filename"], "content_type": d["content_type"],
                    "has_preview": bool(d.get("preview_path"))})
    return out


async def hold(store, tid: str, a: dict) -> None:
    async with use_tenant(tid):
        for f in a["files"]:
            await wrap(store).claim_uploads.update_one(
                {"file_id": f["file_id"], "msg_id": a["draft_id"], "user_id": a["requested_by"], "status": "staged"},
                {"$set": {"held_by": a["approval_id"], "doc.label": f["label"]}})


async def release(store, tid: str, approval_id: str) -> None:
    async with use_tenant(tid):
        await wrap(store).claim_uploads.update_many({"held_by": approval_id, "status": "staged"}, {"$unset": {"held_by": ""}})


async def staged_doc(store, tid: str, a: dict, file_id: str) -> dict | None:
    if file_id not in (a.get("file_ids") or []):
        return None
    async with use_tenant(tid):
        r = await wrap(store).claim_uploads.find_one({"file_id": file_id, "msg_id": a["draft_id"], "user_id": a["requested_by"]})
    return r and r["doc"]


async def _verify(store, tid: str, a: dict) -> None:
    async with use_tenant(tid):
        rows = {r["file_id"]: r async for r in wrap(store).claim_uploads.find({"file_id": {"$in": a["file_ids"]}})}
    if files_hash(a["files"]) != a["files_sha256"]:
        raise _err(409, "approval_files_changed", CHANGED)
    for f in a["files"]:
        r = rows.get(f["file_id"])
        if (not r or r["status"] != "staged" or r.get("held_by") != a["approval_id"] or r["msg_id"] != a["draft_id"]
                or r["user_id"] != a["requested_by"] or r["doc"]["sha256"] != f["sha256"] or r["doc"].get("label") != f.get("label")):
            raise _err(409, "approval_files_changed", CHANGED)
        data, _ = await object_store.get_object(r["doc"]["storage_path"])
        if hashlib.sha256(data).hexdigest() != f["sha256"]:
            raise _err(409, "approval_files_changed", CHANGED)


async def _refuse(store, session, a: dict, status: str, why: str) -> None:
    from app.kernel import notify
    async with use_tenant(session.tenant_id):
        await wrap(store).reply_advice_approvals.update_one({"approval_id": a["approval_id"], "status": "pending"},
                                                           {"$set": {"status": status, "decided_by": session.user_id,
                                                                     "decided_at": datetime.now(timezone.utc), "reason": why}})
    await release(store, session.tenant_id, a["approval_id"])
    await inbox._emit(store, session.tenant_id, "PersonalAdviceRefused", {"approval_id": a["approval_id"], "thread_id": a["thread_id"],
                      "status": status, "file_ids": a["file_ids"], "files_sha256": a["files_sha256"]}, f"advice_refused:{a['approval_id']}",
                      {"kind": "human", "id": session.user_id, "name": session.active_role})
    await notify.deliver(store, session.tenant_id, a["requested_by"], kind="advice_approval", title="Your reply wasn't sent",
                         many="Replies weren't sent", body=why[:140], link=f"/o/inbox/{a['thread_id']}",
                         dedupe_key=f"advice_refused:{a['approval_id']}", idem=f"advice_refused:{a['approval_id']}")


async def approve_and_send(store, session, thread_id: str, a: dict) -> dict:
    """Principal approves text + files: re-verify, then exactly one reply (as the author) with every file attached."""
    c = await claim_for(store, session.tenant_id, thread_id)
    if ct.is_closed(c):
        await _refuse(store, session, a, "refused", "The claim was closed while your reply waited for approval, so it wasn't sent.")
        raise _err(409, "claim_closed", "This claim is closed, so the reply can't be sent. The author has been told.")
    try:
        await _verify(store, session.tenant_id, a)
    except HTTPException:
        await _refuse(store, session, a, "refused", CHANGED)
        raise
    async with use_tenant(session.tenant_id):
        res = await wrap(store).reply_advice_approvals.update_one(
            {"approval_id": a["approval_id"], "status": "pending"},
            {"$set": {"status": "approved", "decided_by": session.user_id, "decided_at": datetime.now(timezone.utc)}})
    if not res.modified_count:
        raise HTTPException(status_code=404, detail="approval_not_found")
    await inbox._emit(store, session.tenant_id, "PersonalAdviceApproved", {
        "approval_id": a["approval_id"], "thread_id": thread_id, "file_ids": a["file_ids"],
        "files": [{"file_id": f["file_id"], "sha256": f["sha256"], "label": f["label"]} for f in a["files"]],
        "files_sha256": a["files_sha256"]}, f"advice_approved:{a['approval_id']}", {"kind": "human", "id": session.user_id, "name": "principal"})
    author = SimpleNamespace(tenant_id=session.tenant_id, user_id=a["requested_by"], active_role=a.get("requested_role") or "ae")
    r = await inbox.reply(store, author, thread_id, a["body"], f"approved-{a['approval_id']}", a["approval_id"],
                          file_ids=a["file_ids"], attachment_summary=ct.summary(a["files"], broker=True))
    await ct.attach(store, session.tenant_id, c, file_ids=a["file_ids"], msg_id=a["draft_id"], user_id=a["requested_by"],
                    role=author.active_role, reply_id=r["reply_id"], approval_id=a["approval_id"], approved_by=session.user_id)
    return {"approval_id": a["approval_id"], "status": "used", "reply_id": r["reply_id"]}
