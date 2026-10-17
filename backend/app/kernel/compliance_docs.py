"""R1.7 — disclosure documents (FSG / PDS / TMD / wording), delivery records, retail/wholesale flag, DDO TMD-fit checks.

Records and workflows that support the brokerage's obligations; they don't make anything legally compliant.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from app.db.tenant_safe import wrap
from app.kernel import object_store
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch
from app.kernel.trail_mongo import DuplicateIdempotencyKey

KINDS = ("FSG", "PDS", "TMD", "WORDING")
DEFAULTS = {
    "gaw_text": ("General advice warning: this information is general advice only. It doesn't take into account your "
                 "objectives, financial situation or needs. Before you decide, consider whether it's right for you and "
                 "read the Product Disclosure Statement and Target Market Determination provided."),
    "afca_text": ("If you're not satisfied with our response, you can contact the Australian Financial Complaints Authority "
                  "(AFCA): www.afca.org.au, info@afca.org.au, 1800 931 678 (free call), GPO Box 3, Melbourne VIC 3001. "
                  "Time limits may apply."),
    "privacy_policy_text": "Our privacy policy explains how we collect, use, hold and disclose your personal information.",
    "collection_notice_text": ("We collect your personal information to arrange and manage your insurance. We share it with "
                               "insurers and service providers as needed. Without it we may not be able to help you."),
    "soa_note": "Personal advice: a Statement of Advice (SoA) is required before the client acts on this recommendation.",
}


class Missing(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc)


async def settings(store, tid) -> dict:
    async with use_tenant(tid):
        row = await wrap(store).tenant_settings.find_one({}, {"_id": 0}) or {}
    return {k: row.get(k) or v for k, v in DEFAULTS.items()}


async def _event(store, tid, typ, payload, idem, by="system", role="compliance"):
    try:
        await append_via_dispatch(store, tid, typ, actor={"kind": "human" if by != "system" else "system", "id": by, "name": role},
                                  payload=payload, idempotency_key=idem)
    except DuplicateIdempotencyKey:
        pass


async def upload(store, tid, *, kind, data: bytes, filename: str, content_type: str, version: str, effective_date: str,
                 insurer: str | None, product: str | None, by: str) -> dict:
    if kind not in KINDS:
        raise Missing(f"Document type must be one of {', '.join(KINDS)}.")
    if kind != "FSG" and not (insurer or "").strip():
        raise Missing("PDS, TMD and wording need the insurer.")
    sha = hashlib.sha256(data).hexdigest()
    path = f"{object_store.APP_NAME}/disclosure/{tid}/{uuid.uuid4()}-{filename[-60:]}"
    await object_store.put_object(path, data, content_type or "application/pdf")
    doc = {"doc_id": str(uuid.uuid4()), "kind": kind, "insurer": (insurer or "").strip() or None, "product": (product or "").strip() or None,
           "version": version.strip()[:40], "effective_date": effective_date, "sha256": sha, "storage_path": path,
           "filename": filename[-120:], "content_type": content_type, "size": len(data), "uploaded_by": by, "uploaded_at": _now(),
           "active": True}
    safe = wrap(store)
    async with use_tenant(tid):
        q = {"kind": kind, "insurer": doc["insurer"], "product": doc["product"], "active": True}
        await safe.disclosure_docs.update_many(q, {"$set": {"active": False, "superseded_by": doc["doc_id"]}})
        await safe.disclosure_docs.insert_one(dict(doc))
    await _event(store, tid, "DisclosureDocumentUploaded", {k: doc[k] for k in ("doc_id", "kind", "insurer", "product", "version",
                 "effective_date", "sha256")}, f"disclosure_doc:{doc['doc_id']}", by, "staff")
    return doc


async def active(store, tid, kind, insurer=None, product=None) -> dict | None:
    async with use_tenant(tid):
        rows = [d async for d in wrap(store).disclosure_docs.find({"kind": kind, "active": True}, {"_id": 0})]
    if kind == "FSG":
        return rows[0] if rows else None
    ins = (insurer or "").strip().lower()
    hits = [d for d in rows if (d.get("insurer") or "").lower() == ins]
    exact = [d for d in hits if (d.get("product") or "").lower() == (product or "").strip().lower()]
    return (exact or [d for d in hits if not d.get("product")] or [None])[0]


async def deliver(store, tid, doc: dict, *, client_id: str, user_id: str | None, channel: str, context: str) -> dict:
    """One DocumentDelivered event per (doc, client, context)."""
    safe = wrap(store)
    async with use_tenant(tid):
        hit = await safe.doc_deliveries.find_one({"doc_id": doc["doc_id"], "client_id": client_id, "context": context}, {"_id": 0})
        if hit:
            return hit
        row = {"delivery_id": str(uuid.uuid4()), "doc_id": doc["doc_id"], "kind": doc["kind"], "version": doc["version"],
               "sha256": doc["sha256"], "insurer": doc.get("insurer"), "product": doc.get("product"), "filename": doc["filename"],
               "client_id": client_id, "recipient_user_id": user_id, "channel": channel, "context": context, "at": _now()}
        await safe.doc_deliveries.insert_one(dict(row))
    await _event(store, tid, "DocumentDelivered", {k: row[k] for k in ("delivery_id", "doc_id", "kind", "version", "sha256", "client_id",
                 "recipient_user_id", "channel", "context")} | {"delivered_at": row["at"].isoformat()},
                 f"doc_delivered:{doc['doc_id']}:{client_id}:{context}")
    return row


async def on_client_onboarded(store, tid, client_id, user_id, channel) -> dict | None:
    """Invite acceptance: deliver the FSG and record that the collection notice + privacy policy were shown."""
    st = await settings(store, tid)
    sha = hashlib.sha256((st["collection_notice_text"] + "\x1f" + st["privacy_policy_text"]).encode()).hexdigest()
    await _event(store, tid, "CollectionNoticeRecorded", {"client_id": client_id, "user_id": user_id, "channel": channel,
                 "notice_sha256": sha}, f"collection_notice:{client_id}:{user_id}:{sha[:16]}", user_id, "client")
    return await deliver_fsg(store, tid, client_id, user_id, channel)


async def deliver_fsg(store, tid, client_id, user_id, channel, context="first_service") -> dict | None:
    """Before the first service: once per client per FSG version (re-delivered when a new version is uploaded)."""
    fsg = await active(store, tid, "FSG")
    if not fsg:
        await _event(store, tid, "DocumentDeliveryMissing", {"kind": "FSG", "client_id": client_id, "context": context},
                     f"doc_missing:FSG:{client_id}:{context}:{_now().date()}")
        return None
    return await deliver(store, tid, fsg, client_id=client_id, user_id=user_id, channel=channel, context=f"fsg:{fsg['version']}")


async def has_fsg(store, tid, client_id) -> bool:
    async with use_tenant(tid):
        return bool(await wrap(store).doc_deliveries.find_one({"client_id": client_id, "kind": "FSG"}))


async def client_type(store, tid, client_id) -> dict:
    async with use_tenant(tid):
        c = await wrap(store).clients.find_one({"client_id": client_id}) or {}
    return {"client_type": c.get("client_type") or "retail", "reason": c.get("client_type_reason"),
            "default": not c.get("client_type")}


async def set_client_type(store, tid, client_id, kind, reason, by) -> dict:
    if kind not in ("retail", "wholesale") or (kind == "wholesale" and not (reason or "").strip()):
        raise Missing("Wholesale needs the test it meets (e.g. 'business with 20+ employees', 'professional investor').")
    async with use_tenant(tid):
        await wrap(store).clients.update_one({"client_id": client_id}, {"$set": {"client_type": kind, "client_type_reason": reason}})
    await _event(store, tid, "ClientTypeSet", {"client_id": client_id, "client_type": kind, "reason": reason},
                 f"client_type:{client_id}:{_now().isoformat()}", by, "staff")
    return await client_type(store, tid, client_id)


async def deliver_quote_docs(store, tid, *, client_id, user_id, options: list[dict], context: str) -> list[dict]:
    """Retail clients: PDS + TMD for every option, delivered with the option. Raises Missing listing what to upload first."""
    if (await client_type(store, tid, client_id))["client_type"] != "retail":
        return []
    need, missing = [], []
    for o in options:
        for kind in ("PDS", "TMD"):
            d = await active(store, tid, kind, o["insurer"], o.get("product"))
            (need if d else missing).append(d or f"{kind} for {o['insurer']}")
    if missing:
        raise Missing("Upload these before showing options to a retail client: " + ", ".join(dict.fromkeys(missing)) + ".")
    return [await deliver(store, tid, d, client_id=client_id, user_id=user_id, channel="in_app", context=context) for d in need]


async def deliveries_for(store, tid, client_ids) -> list[dict]:
    async with use_tenant(tid):
        return [d async for d in wrap(store).doc_deliveries.find({"client_id": {"$in": list(client_ids)}}, {"_id": 0}).sort("at", -1)]


async def ensure_indexes(store) -> None:
    # tenant-safe: system-only — index DDL
    await store.doc_deliveries.create_index([("tenant_id", 1), ("doc_id", 1), ("client_id", 1), ("context", 1)], unique=True)
    await store.disclosure_docs.create_index([("tenant_id", 1), ("doc_id", 1)], unique=True)
    await store.complaints.create_index([("tenant_id", 1), ("matter_id", 1)], unique=True)
    await store.privacy_requests.create_index([("tenant_id", 1), ("matter_id", 1)], unique=True)
