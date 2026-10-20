"""R2.5 — document emails: the renewal comparison PDF to every client member who can view the client.

Durable rows in doc_emails (idempotent per renewal + PDF hash + recipient), sent by the periodic "docemail.send" job through
the same gates as alert emails: EMAIL_ENABLED, the tenant allow-list, reserved .example domains, quiet hours, retries, and the
email_override. The client pref "Documents by email" (default on) is separate from alert emails.
"""
from __future__ import annotations

import base64
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from html import escape

from pymongo.errors import DuplicateKeyError

from app.db.tenant_safe import wrap
from app.kernel import comparison_pdf as cp
from app.kernel import email_channel as ec
from app.kernel import compliance_docs, object_store
from app.kernel.holidays import SYD
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch

log = logging.getLogger(__name__)
RESEND_THROTTLE_S = 600


async def enqueue_comparison(store, tid: str, r: dict, *, by: str, resend: bool = False) -> list[str]:
    """Queue one email per member. Same PDF hash + recipient twice is a no-op unless it's an explicit (ledgered) resend."""
    _pdf, sha = await cp.document(store, tid, r, by)
    safe = wrap(store)
    await store.doc_emails.create_index([("tenant_id", 1), ("idem", 1)], unique=True)  # tenant-safe: system-only — index
    out = []
    async with use_tenant(tid):
        members = [m["user_id"] async for m in safe.memberships.find({"client_id": r["client_id"], "revoked_at": None}, {"user_id": 1})]
        for uid in dict.fromkeys(members):
            prior = [p async for p in safe.doc_emails.find({"renewal_id": r["renewal_id"], "user_id": uid}, {"_id": 0, "sha256": 1})]
            row = {"email_id": str(uuid.uuid4()), "user_id": uid, "renewal_id": r["renewal_id"], "envelope_id": r["envelope_id"],
                   "client_id": r["client_id"], "policy_class": r.get("product") or "your policy", "sha256": sha,
                   "updated": any(p["sha256"] != sha for p in prior), "resend": resend,
                   "idem": f"cmp:{r['renewal_id']}:{sha}:{uid}" + (f":resend:{uuid.uuid4()}" if resend else ""),
                   "channels_state": {"email": "pending"}, "attempts": 0, "next_at": None,
                   "created_at": datetime.now(timezone.utc), "test": bool(r.get("test"))}
            try:
                await safe.doc_emails.insert_one(row)
                out.append(row["email_id"])
            except DuplicateKeyError:
                pass
    return out


def _filename(policy_class: str, when: datetime) -> str:
    cls = re.sub(r"[^A-Za-z0-9]+", "-", policy_class).strip("-") or "policy"
    return f"Renewal-options-{cls}-{when.astimezone(SYD):%d-%m-%Y}.pdf"


def render(row: dict, user: dict, settings: dict, gaw: str) -> tuple[str, str, str]:
    """Short, plain, fixed template. No premiums, CRN or bank details (the PDF carries the comparison)."""
    brand, colour, base = settings["sender_name"], settings["brand_colour"], os.environ["APP_BASE_URL"].rstrip("/")
    cls = row["policy_class"]
    subject = f"{'Updated: your' if row['updated'] else 'Your'} renewal options for {cls}"
    url = f"{base}/c/renewal/{row['renewal_id']}"
    first = ec.greet_name(user.get("display_name"))
    lead = ("Your broker has updated your renewal options." if row["updated"] else "Your renewal options are ready.")
    lines = [lead, "Your broker recommends one of them. The attached PDF compares them side by side.",
             "Review the options and accept your choice in the app."]
    text = (f"Hi {first},\n\n" + "\n".join(lines) + f"\n\nOpen your renewal: {url}\n\n{gaw}\n\n"
            "Turn document emails off in You → Notifications.\nSent by " + brand + ". We never ask for passwords or card details by email.")
    html = (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F4F6FA;padding:24px 0">'
        '<tr><td align="center"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="max-width:560px;background:#FFFFFF;border-radius:12px;font-family:Arial,Helvetica,sans-serif;color:#0B1B3A">'
        f'<tr><td style="padding:20px 28px;border-bottom:3px solid {colour};font-size:15px;font-weight:bold">{escape(brand)}</td></tr>'
        f'<tr><td style="padding:24px 28px 8px;font-size:20px;font-weight:bold">{escape(subject)}</td></tr>'
        f'<tr><td style="padding:0 28px 20px;font-size:15px;line-height:22px">Hi {escape(first)},<br>'
        + "<br>".join(escape(x) for x in lines) + '</td></tr>'
        f'<tr><td style="padding:0 28px 24px"><a href="{escape(url)}" style="display:inline-block;background:{colour};'
        'color:#FFFFFF;text-decoration:none;padding:12px 22px;border-radius:999px;font-size:14px;font-weight:bold">'
        'Review and accept</a></td></tr>'
        f'<tr><td style="padding:0 28px 20px;font-size:12px;line-height:18px;color:#5B6B85">{escape(gaw)}</td></tr>'
        '<tr><td style="padding:16px 28px;border-top:1px solid #E3E8F0;font-size:12px;color:#5B6B85">'
        f'Turn document emails off in You → Notifications.<br>Sent by {escape(brand)}. '
        'We never ask for passwords or card details by email.</td></tr></table></td></tr></table>')
    return subject, html, text


async def run_once(store, now: datetime | None = None, *, send=None, allow: list[str] | None = None,
                   ids: list[str] | None = None, ignore_quiet: bool = False) -> dict:
    # tenant-safe: system-only — the sender spans tenants; every row is handled by its own id.
    now, send = now or datetime.now(timezone.utc), (ec.resend_send if send is None else send)
    q: dict = {"channels_state.email": {"$in": ["pending", "held", "retry"]}, "$or": [{"next_at": None}, {"next_at": {"$lte": now}}]}
    if ids:
        q["email_id"] = {"$in": ids}
    from app.kernel.platform import hold_suspended
    q["tenant_id"] = {"$nin": await hold_suspended(store, "doc_emails", "channels_state.email")}  # R2.6
    out: dict = {}
    async for row in store.doc_emails.find(q).limit(100):
        status = await _process(store, row, now, send, allow, ignore_quiet)
        out[status] = out.get(status, 0) + 1
    return out


async def _set(store, row, status, **extra):
    await store.doc_emails.update_one({"_id": row["_id"]}, {"$set": {"channels_state.email": status, **extra}})  # tenant-safe: by _id
    return status


async def _process(store, row, now, send, allow, ignore_quiet) -> str:
    tid = row["tenant_id"]
    if not ec.email_enabled():
        return await _set(store, row, "skipped:email_disabled")
    user = await store.users.find_one({"user_id": row["user_id"], "tenant_id": tid}) or {}  # tenant-safe: scoped
    if user.get("disabled"):
        return await _set(store, row, "skipped:user_disabled")
    prefs = await store.notification_prefs.find_one({"user_id": row["user_id"], "tenant_id": tid}) or {}  # tenant-safe: scoped
    if not prefs.get("documents_email", True):
        return await _set(store, row, "skipped:pref_off")
    settings = await ec.tenant_settings(store, tid)
    addr, why = ec.resolve_address(user, settings["allowlist"] if allow is None else allow)
    if why:
        return await _set(store, row, why)
    if ec.in_quiet_hours(now) and not ignore_quiet:
        return await _set(store, row, "held", next_at=ec.next_send_time(now))
    claimed = await store.doc_emails.update_one({"_id": row["_id"], "channels_state.email": row["channels_state"]["email"]},  # tenant-safe: by _id
                                                {"$set": {"channels_state.email": "sending"}})
    if not claimed.modified_count:
        return "raced"
    safe = wrap(store)
    async with use_tenant(tid):
        doc = await safe.comparison_docs.find_one({"renewal_id": row["renewal_id"], "sha256": row["sha256"]}, {"_id": 0})
    pdf, _ = await object_store.get_object(doc["storage_path"])
    gaw = (await compliance_docs.settings(store, tid))["gaw_text"]
    subject, html, text = render(row, user, settings, gaw)
    fname, attempts = _filename(row["policy_class"], now), row.get("attempts", 0) + 1
    try:
        mid = await send(to=addr, subject=subject, html=html, text=text, from_name=settings["sender_name"],
                         reply_to=settings["reply_to"],
                         attachments=[{"filename": fname, "content": base64.b64encode(pdf).decode(), "content_type": "application/pdf"}])
    except Exception as e:  # noqa: BLE001 — never blocks the in-app delivery
        log.warning("document email failed (attempt %s): %s", attempts, ec._scrubbed(e))
        if attempts >= ec.MAX_ATTEMPTS:
            return await _set(store, row, "failed", attempts=attempts, error=str(e)[:200])
        return await _set(store, row, "retry", attempts=attempts, next_at=now + ec.BACKOFF[attempts - 1], error=str(e)[:200])
    await _set(store, row, "sent", message_id=mid, sent_at=now, attempts=attempts, filename=fname)
    async with use_tenant(tid):
        await safe.doc_deliveries.insert_one({
            "delivery_id": str(uuid.uuid4()), "doc_id": f"comparison:{row['sha256']}", "kind": "RENEWAL_COMPARISON", "version": None,
            "sha256": row["sha256"], "client_id": row["client_id"], "user_id": row["user_id"], "channel": "email",
            "context": f"renewal:{row['renewal_id']}:email:{row['email_id']}", "message_id": mid, "at": now})
        await append_via_dispatch(store, tid, "DocumentDelivered", actor={"kind": "system", "id": "docemail.send", "name": "email"}, payload={
            "kind": "RENEWAL_COMPARISON", "channel": "email", "sha256": row["sha256"], "recipient_user_id": row["user_id"],
            "client_id": row["client_id"], "renewal_id": row["renewal_id"], "envelope_id": row["envelope_id"], "message_id": mid,
            "updated": row["updated"]}, idempotency_key=f"docemail:{row['email_id']}")
    return "sent"


async def status_for(store, tid: str, renewal_id: str) -> list[dict]:
    safe = wrap(store)
    async with use_tenant(tid):
        rows = [x async for x in safe.doc_emails.find({"renewal_id": renewal_id}, {"_id": 0}).sort("created_at", -1).limit(20)]
        names = {u["user_id"]: u.get("display_name") or "client" async for u in safe.users.find(
            {"user_id": {"$in": [x["user_id"] for x in rows]}}, {"_id": 0, "user_id": 1, "display_name": 1})}
    return [{"name": names.get(x["user_id"], "client"), "status": x["channels_state"]["email"], "sent_at": x.get("sent_at"),
             "created_at": x["created_at"], "updated": x["updated"], "resend": x.get("resend", False), "message_id": x.get("message_id")}
            for x in rows]
