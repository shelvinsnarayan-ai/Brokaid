"""R1.7 — APP 12 access export: one client's data as JSON + a PDF summary, built by a durable job, link valid 7 days."""
from __future__ import annotations

import io
import json
import uuid
from datetime import datetime, timedelta, timezone

from app.db.tenant_safe import wrap
from app.kernel import object_store
from app.kernel.tenant_ctx import use_tenant

TTL = timedelta(days=7)
SECRET = {"password_hash", "mfa_secret", "recovery_codes", "token_hash", "storage_path", "signature_blob_hash"}


def _clean(v):
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items() if k not in SECRET and k != "_id"}
    if isinstance(v, list):
        return [_clean(x) for x in v]
    return v.isoformat() if isinstance(v, datetime) else v


async def collect(store, tid, client_id, user_id) -> dict:
    safe = wrap(store)
    async with use_tenant(tid):
        f = {"client_id": client_id}

        async def rows(coll, q=None, limit=2000):
            return [d async for d in getattr(safe, coll).find(q or f).limit(limit)]
        pids = [p["policy_id"] for p in await rows("policies")]
        out = {
            "client": await safe.clients.find_one(f),
            "you": await safe.users.find_one({"user_id": user_id}, {"display_name": 1, "email": 1, "user_id": 1}),
            "memberships": await rows("memberships"), "policies": await rows("policies"), "renewals": await rows("renewals"),
            "payments": await rows("payables", {"policy_id": {"$in": pids}}), "documents_provided": await rows("doc_deliveries"),
            "complaints": await rows("complaints"), "privacy_requests": await rows("privacy_requests"),
            "notifications": await rows("notifications", {"user_id": user_id}, 500),
            "activity": [{"type": e["type"], "at": e.get("ts")} async for e in
                         safe.events.find({"payload.client_id": client_id}, {"type": 1, "ts": 1}).limit(2000)],
        }
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "tenant_id": tid, "client_id": client_id, **_clean(out)}


def pdf(data: dict) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    lines = [f"Your data — {(data.get('client') or {}).get('display_name')}", f"Prepared {data['generated_at'][:16]} UTC", ""]
    for k in ("policies", "renewals", "payments", "documents_provided", "complaints", "privacy_requests", "notifications", "activity"):
        lines.append(f"{k.replace('_', ' ').title()}: {len(data.get(k) or [])} record(s)")
    for p in data.get("policies") or []:
        lines.append(f"  • {p.get('product')} — {p.get('insurer_display')} — ends {str(p.get('expiry'))[:10]} ({p.get('status')})")
    lines += ["", "The JSON file has every field we hold for you. Contact your broker to correct anything."]
    for ln in lines:
        c.drawString(48, y, ln[:110])
        y -= 16
        if y < 60:
            c.showPage()
            y = 800
    c.save()
    return buf.getvalue()


async def build(store, tid, matter_id, client_id, user_id) -> dict:
    data = await collect(store, tid, client_id, user_id)
    eid = str(uuid.uuid4())
    base = f"{object_store.APP_NAME}/privacy/{tid}/{eid}"
    await object_store.put_object(f"{base}.json", json.dumps(data, indent=2, default=str).encode(), "application/json")
    await object_store.put_object(f"{base}.pdf", pdf(data), "application/pdf")
    now = datetime.now(timezone.utc)
    row = {"export_id": eid, "matter_id": matter_id, "client_id": client_id, "user_id": user_id, "path": base,
           "created_at": now, "expires_at": now + TTL, "counts": {k: len(v) for k, v in data.items() if isinstance(v, list)}}
    async with use_tenant(tid):
        await wrap(store).privacy_exports.insert_one(dict(row))
    return row
