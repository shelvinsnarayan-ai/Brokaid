"""R3.4 — claim message threads: one inbox thread per claim (thread_id "claim-<claim_id>"), on the existing ledgered Ask model.

Client messages are ClientQuestionAsked events carrying claim_id (no AI auto-reply); broker replies use inbox.reply unchanged.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.db.tenant_safe import wrap
from app.kernel import inbox
from app.kernel.tenant_ctx import use_tenant

CLOSED = ("closed", "withdrawn")
CLOSED_NOTE = "This claim is closed. Ask your broker in the main thread if you need anything."
PREFIX = "claim-"


def thread_id_for(claim_id: str) -> str:
    return f"{PREFIX}{claim_id}"


def is_closed(c: dict) -> bool:
    return (c.get("stage") or c.get("status")) in CLOSED


_PRODUCT_CLASS = (("liability", "Liability"), ("cyber", "Cyber"), ("motor", "Motor"), ("vehicle", "Motor"), ("fleet", "Motor"),
                  ("tools", "Tools"), ("contents", "Property"), ("pack", "Property"), ("building", "Property"), ("property", "Property"),
                  ("marine", "Marine"))


def policy_class(product: str | None) -> str | None:
    p = (product or "").lower()
    return next((v for k, v in _PRODUCT_CLASS if k in p), None)


def label(c: dict, things: dict | None = None) -> str:
    """'Motor claim · Ford Ranger XLT XYZ-789'; without a vehicle, the policy name, and the policy's own class when the
    claim says motor but the policy isn't a motor/fleet one (e.g. 'Liability claim · Marine Liability')."""
    cls = (c.get("claim_class") or c.get("claim_type") or "other").split("_")[0].lower()
    rego = (c.get("incident") or {}).get("rego")
    if cls == "motor" and rego:
        thing = (things or {}).get(str(rego).upper())
        return f"Motor claim · {thing} {rego}" if thing else f"Motor claim · {rego}"
    product, pcls = c.get("product"), policy_class(c.get("product"))
    title = (pcls or "Other") if (cls == "motor" and pcls != "Motor") or cls == "other" else cls.title()
    return f"{title} claim · {product or c.get('claim_reference')}"


async def vehicle_names(store, tid: str, client_id: str) -> dict:
    out = {}
    async with use_tenant(tid):
        async for p in wrap(store).policies.find({"client_id": client_id, "schedule.vehicles": {"$exists": True}}, {"schedule.vehicles": 1}):
            for v in p["schedule"]["vehicles"]:
                if v.get("rego"):
                    out[str(v["rego"]).upper()] = " ".join(str(x) for x in (v.get("make"), v.get("model")) if x)
    return out


async def thread_for(store, tid: str, client_id: str, claim_id: str, include_tests: bool = False) -> dict | None:
    tid_ = thread_id_for(claim_id)
    return next((t for t in await inbox.load_threads(store, tid, client_id=client_id, include_tests=include_tests)
                 if t["thread_id"] == tid_), None)


async def post_client(store, tid: str, *, user_id: str, role: str, claim: dict, message: str, msg_id: str | None = None,
                      received_at: datetime | None = None, file_ids: list[str] | None = None, attachment_summary: str | None = None,
                      promise: bool = True) -> dict:
    """Ledgered client message on the claim thread. A reply-by promise starts only when nothing is already awaiting a reply."""
    thread_id = thread_id_for(claim["claim_id"])
    msg_id = msg_id or str(uuid.uuid4())
    received = received_at or datetime.now(timezone.utc)
    t = await thread_for(store, tid, claim["client_id"], claim["claim_id"], include_tests=True)
    payload = {"envelope_id": thread_id, "client_id": claim["client_id"], "claim_id": claim["claim_id"], "message": message,
               "received_at": inbox._utc(received).isoformat(), "msg_id": msg_id}
    if file_ids:
        payload |= {"file_ids": list(file_ids), "attachment_summary": attachment_summary}
    if claim.get("test"):
        payload["test"] = True
    ev = await inbox._emit(store, tid, "ClientQuestionAsked", payload, f"ask:in:{thread_id}:{msg_id}",
                           actor={"kind": "human", "id": user_id, "name": role})
    if promise and ev is not None and (t is None or t["answered"]):
        from app.kernel.holidays import state_for
        await inbox._emit(store, tid, "PromiseMade", {
            "thread_id": thread_id, "client_id": claim["client_id"], "claim_id": claim["claim_id"], "received_at": inbox._utc(received).isoformat(),
            "reply_by": inbox.reply_by_for(inbox._utc(received), await state_for(store, tid)).isoformat(), "data_basis": inbox.BASIS,
            "msg_id": msg_id},
            f"promise:{thread_id}:{msg_id}")
    return {"thread_id": thread_id, "msg_id": msg_id, "replayed": ev is None}


# ---- R3.5 attachments: staged per draft message (claim_uploads), attached as ordinary claim files when the message is sent ----

_NOUN = {"photo": ("photo", "photos"), "video": ("video", "videos"), "repair_quote": ("repair quote", "repair quotes"),
         "assessor_report": ("assessor report", "assessor reports"), "insurer_letter": ("insurer letter", "insurer letters"),
         "other": ("document", "documents")}


def summary(docs: list[dict], broker: bool = False) -> str | None:
    """'2 photos', '1 photo and 1 video' (client); 'a repair quote and 2 photos' (broker, by label)."""
    counts: dict[str, int] = {}
    for d in docs:
        k = (d.get("label") or d.get("kind")) if broker else d.get("kind")
        k = k if k in _NOUN else "other"
        counts[k] = counts.get(k, 0) + 1
    parts = [f"{('an' if _NOUN[k][0][0] in 'aeiou' else 'a') if broker and n == 1 else n} {_NOUN[k][n != 1]}" for k, n in counts.items()]
    return (", ".join(parts[:-1]) + " and " + parts[-1] if len(parts) > 1 else parts[0]) if parts else None


def attachments(c: dict, file_ids: list[str]) -> list[dict]:
    docs = {d["file_id"]: d for d in c.get("docs") or []}
    return [{"file_id": f, "kind": docs[f]["kind"], "content_type": docs[f]["content_type"], "filename": docs[f]["filename"],
             "size": docs[f]["size"], "duration_s": docs[f].get("duration_s"), "has_preview": bool(docs[f].get("preview_path")),
             "label": docs[f].get("label")}
            for f in file_ids if f in docs]


async def message_sent(store, tid: str, claim_id: str, msg_id: str) -> bool:
    async with use_tenant(tid):
        return bool(await wrap(store).events.find_one({"type": "ClientQuestionAsked", "payload.claim_id": claim_id, "payload.msg_id": msg_id}))


async def staged(store, tid: str, claim_id: str, msg_id: str, user_id: str) -> list[dict]:
    async with use_tenant(tid):
        return [r async for r in wrap(store).claim_uploads.find({"claim_id": claim_id, "msg_id": msg_id, "user_id": user_id,
                                                                 "status": "staged"}, {"_id": 0})]


async def stage(store, tid: str, c: dict, *, src: str, content_type: str, filename: str, user_id: str, msg_id: str,
                role: str = "client", label: str | None = None) -> dict:
    """Strip metadata (fail closed), store the clean file (+ JPEG preview for HEIC / poster for video) and stage it for the draft."""
    import asyncio
    import hashlib
    import os
    import re
    import tempfile

    from app.kernel import media, object_store
    kind, info, preview = media.kind_of(content_type), {}, None
    with tempfile.TemporaryDirectory(prefix="r35-") as d:
        if kind == "video":
            dst, poster = os.path.join(d, f"clean.{media.EXT[content_type]}"), os.path.join(d, "poster.jpg")
            info = await asyncio.to_thread(media.strip_video, src, dst, poster)
            with open(dst, "rb") as f:
                data = f.read()
            with open(poster, "rb") as f:
                preview = await asyncio.to_thread(media.jpeg_preview, f.read(), 640)
        elif kind == "document":  # R3.6: PDFs are checked, then stored exactly as uploaded
            with open(src, "rb") as f:
                data = f.read()
            await asyncio.to_thread(media.check_pdf, data)
        else:
            with open(src, "rb") as f:
                data = await asyncio.to_thread(media.strip_photo, f.read(), content_type)
            if content_type == "image/heic":
                preview = await asyncio.to_thread(media.jpeg_preview, data)
    stem = re.sub(r"[^\w.-]", "_", os.path.splitext(filename or kind)[0])[-50:] or kind
    name = f"{stem}.{media.EXT[content_type]}"
    path = f"{object_store.APP_NAME}/claims/{tid}/{c['claim_id']}/{uuid.uuid4()}-{name}"
    await object_store.put_object(path, data, content_type)
    if preview:
        await object_store.put_object(f"{path}.preview.jpg", preview, "image/jpeg")
    now = datetime.now(timezone.utc)
    doc = {"file_id": str(uuid.uuid4()), "kind": kind, "filename": name, "content_type": content_type, "size": len(data),
           "sha256": hashlib.sha256(data).hexdigest(), "storage_path": path, "preview_path": f"{path}.preview.jpg" if preview else None,
           "duration_s": info.get("duration_s"), "uploaded_by": user_id, "uploaded_role": role, "uploaded_at": now,
           "location_kept": False, "client_visible": True, "req_id": None, "source": "claim_thread", "msg_id": msg_id}
    if role != "client":
        doc["label"] = label or media.default_label(content_type)
    async with use_tenant(tid):
        await wrap(store).claim_uploads.insert_one({"file_id": doc["file_id"], "claim_id": c["claim_id"], "msg_id": msg_id,
                                                    "user_id": user_id, "status": "staged", "at": now, "doc": doc})
    return doc


async def discard(store, tid: str, claim_id: str, file_id: str, user_id: str) -> bool:
    async with use_tenant(tid):
        r = await wrap(store).claim_uploads.update_one({"file_id": file_id, "claim_id": claim_id, "user_id": user_id, "status": "staged",
                                                        "held_by": None},
                                                       {"$set": {"status": "discarded"}})
    return bool(r.modified_count)


async def attach(store, tid: str, c: dict, *, file_ids: list[str], msg_id: str, user_id: str, role: str,
                 reply_id: str | None = None, labels: dict | None = None, approval_id: str | None = None,
                 approved_by: str | None = None) -> list[dict]:
    """Staged uploads become claim files: one ClaimDocumentAdded ledger event per file (source claim_thread, msg_id, sha256).
    Files held by a pending approval (R3.7) only attach through that approval."""
    from app.kernel import claims as ck, media
    rows = {r["file_id"]: r["doc"] for r in await staged(store, tid, c["claim_id"], msg_id, user_id)
            if r.get("held_by") in (None, approval_id)}
    if len(set(file_ids)) != len(file_ids) or any(f not in rows for f in file_ids):
        raise ck.Bad("One of the attachments is missing or was removed. Remove it and attach it again.")
    docs = [rows[f] for f in file_ids]
    for d in docs:
        if reply_id:
            d["reply_id"] = reply_id
        if approved_by:
            d |= {"approval_id": approval_id, "approved_by": approved_by}
        if not approval_id and d.get("label") and (labels or {}).get(d["file_id"]) in media.LABELS:
            d["label"] = labels[d["file_id"]]
    if sum(d["size"] for d in docs) > media.MAX_MESSAGE:
        raise ck.Bad("Attachments on one message can add up to 250 MB.")
    for d in docs:
        async with use_tenant(tid):
            await wrap(store).claims.update_one({"claim_id": c["claim_id"], "docs.file_id": {"$ne": d["file_id"]}}, {"$push": {"docs": d}})
            await wrap(store).claim_uploads.update_one({"file_id": d["file_id"]}, {"$set": {"status": "attached"}})
        c.setdefault("docs", []).append(d)
        await ck._event(store, tid, "ClaimDocumentAdded", {"claim_id": c["claim_id"], "file_id": d["file_id"], "kind": d["kind"],
                                                           "sha256": d["sha256"], "size": d["size"], "location_kept": False,
                                                           "source": "claim_thread", "msg_id": msg_id, "uploaded_role": d["uploaded_role"],
                                                           "reply_id": reply_id, "label": d.get("label"), "approved_by": approved_by,
                                                           "approval_id": approval_id, "test": bool(c.get("test"))},
                        f"claim_doc:{d['file_id']}", user_id, role)
    return docs
