"""R0.11A — one notification pipeline: ledger event → notify rule → notifications (+ channel states).

Hooked once, after every successful ledger append (trail_dispatch.append_via_dispatch). Only
in_app is enabled; email and push are channel stubs that record "not_enabled" (phases B and C).
Read/delivery state is UI state: no ledger events are written for it.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from pymongo.errors import DuplicateKeyError

from app.db.tenant_safe import wrap
from app.kernel import inbox
from app.kernel.tenant_ctx import use_tenant

log = logging.getLogger("brokaid.notify")
COALESCE = timedelta(minutes=5)
CHECK_EVERY_S = 300
QUIET_HOURS = {"start": "21:00", "end": "07:00", "tz": "Australia/Sydney", "applies_to": ["email", "push"]}


class InAppChannel:
    name = "in_app"

    async def deliver(self, n: dict, prefs: dict) -> str:
        return "delivered"  # in-app is never held by quiet hours


class NotEnabledChannel:
    """Phase B (Resend email) and C (web push) plug in here; until then nothing is sent."""

    def __init__(self, name: str):
        self.name = name

    async def deliver(self, n: dict, prefs: dict) -> str:
        return "not_enabled"


from app.kernel.email_channel import EmailChannel  # noqa: E402

from app.kernel.push_channel import PushChannel  # noqa: E402

CHANNELS = [InAppChannel(), EmailChannel(), PushChannel()]
DEFAULT_PREFS = {"in_app": True, "email": False, "push": False, "quiet_hours": QUIET_HOURS}

STAFF_REVIEW = dict(kind="broker_review", to=("staff", ("ae",)), title="A request needs a broker",
                    many="{n} requests need a broker", body="{client} has a request waiting for broker review.",
                    link="/o/queue/{envelope_id}")
RULES: dict[str, dict] = {
    "ClientQuestionAsked": dict(kind="client_question", to=("staff", ("ae", "principal")),
                                title="New question from {client}", many="{n} new questions from {client}",
                                body="Reply by {reply_by}.", link="/o/inbox/{thread_id}"),
    "BrokerReplied": dict(kind="broker_reply", to=("client", None), title="Your broker replied",
                          many="Your broker replied ({n} new)", body="{author} replied to your question.",
                          link="/c/ask"),
    "RequestNeedsHumanReview": STAFF_REVIEW,
    "QueueCardEnqueued": STAFF_REVIEW,
    "CertificateRequestNeedsBroker": {**STAFF_REVIEW, "title": "A certificate request needs a broker",
                                      "body": "{client} asked for a certificate that needs broker review."},
    "EndorsementQuoteReady": {**STAFF_REVIEW, "title": "A quote needs broker approval",
                              "many": "{n} quotes need broker approval",
                              "body": "{client} has a quoted change waiting for broker approval."},
    "OperatorApproval": dict(kind="approval_needed", to=("client", "can_approve"), title="Ready for your approval",
                             many="Ready for your approval", body="Your broker has checked your request. Please review and approve it.",
                             link="/c/cover/{policy_id}/pending", once="approval_needed:{envelope_id}"),
    "SignatureRequested": dict(kind="signature_needed", to=("client", "can_sign"), title="Signature needed",
                               many="Signature needed", body="Your change is approved. Please sign to finish it.",
                               link="/c", once="signature_needed:{envelope_id}"),
    "payment.advised": dict(kind="signature_needed", to=("client", "can_sign"), title="Signature needed",
                            many="Signature needed", body="Your change is approved. Please sign to finish it.",
                            link="/c", once="signature_needed:{envelope_id}"),
    "CertificateRegenerated": dict(kind="certificate_issued", to=("client", None), title="Your certificate is ready",
                                   many="Your certificates are ready", body="A new certificate of currency is ready to view.",
                                   link="/c/cover"),
}


async def _staff(safe, roles) -> list[str]:
    return [r["user_id"] async for r in safe.user_brokerage_roles.find({"role": {"$in": list(roles)}, "active": True})]


async def assigned_ae(safe, client_id: str | None) -> str | None:
    """The client's assigned AE if still an active AE (R1.2b)."""
    c = await safe.clients.find_one({"client_id": client_id}) if client_id else None
    uid = (c or {}).get("assigned_ae_user_id")
    if uid and await safe.user_brokerage_roles.find_one({"user_id": uid, "role": "ae", "active": True}):
        return uid
    return None


async def _client_staff(safe, client_id: str | None) -> list[str]:
    """Client-origin alerts: assigned AE + Principal(s); every AE only when nobody is assigned."""
    ae = await assigned_ae(safe, client_id)
    out = [ae] if ae else await _staff(safe, ("ae",))
    return list(dict.fromkeys(out + await _staff(safe, ("principal",))))


async def _members(safe, client_id: str | None, need: str | None) -> list[str]:
    if not client_id:
        return []
    out = []
    async for link in safe.user_client_links.find({"client_id": client_id}):
        m = await safe.memberships.find_one({"user_id": link["user_id"], "client_id": client_id, "revoked_at": None})
        if need is None or (m and m["authority"].get(need)):
            out.append(link["user_id"])
    return out


async def _client_id(safe, p: dict) -> str | None:
    if p.get("client_id"):
        return p["client_id"]
    if p.get("policy_id"):
        pol = await safe.policies.find_one({"policy_id": p["policy_id"]})
        if pol:
            return pol["client_id"]
    env = p.get("envelope_id")
    if env:
        async for e in safe.events.find({"payload.envelope_id": env}).sort("seq", 1).limit(20):
            q = e.get("payload") or {}
            if q.get("client_id") or q.get("policy_id"):
                return await _client_id(safe, {k: q.get(k) for k in ("client_id", "policy_id")})
    return None


async def _prefs(safe, user_id: str) -> dict:
    """Email defaults ON for broker staff, OFF for clients until they opt in (R0.12)."""
    row = await safe.notification_prefs.find_one({"user_id": user_id}) or {}
    user = await safe.users.find_one({"user_id": user_id}) or {}
    base = {**DEFAULT_PREFS, "email": user.get("kind") == "operator"}
    return {**base, **{k: row[k] for k in ("email", "push") if k in row}, "in_app": True}


async def deliver(store, tenant_id: str, user_id: str, *, kind: str, title: str, many: str, body: str,
                  link: str, dedupe_key: str, idem: str, source_event_id: str | None = None,
                  thread_id: str | None = None, reply_id: str | None = None, test: bool = False) -> str:
    """Create or coalesce one notification. Returns 'created' | 'coalesced' | 'duplicate'."""
    safe = wrap(store)
    now = datetime.now(timezone.utc)
    async with use_tenant(tenant_id):
        if await safe.notifications.find_one({"user_id": user_id, "idem_keys": idem}):
            return "duplicate"
        prev = await safe.notifications.find_one({"user_id": user_id, "dedupe_key": dedupe_key, "read_at": None,
                                                  "updated_at": {"$gte": now - COALESCE}})
        if prev:
            n = prev.get("count", 1) + 1
            try:
                await safe.notifications.update_one({"notification_id": prev["notification_id"]}, {
                "$set": {"title": many.format(n=n), "body": body, "link": link, "updated_at": now,
                         "source_event_id": source_event_id, "reply_id": reply_id or prev.get("reply_id")},
                "$inc": {"count": 1}, "$push": {"idem_keys": idem}})
            except DuplicateKeyError:
                return "duplicate"
            return "coalesced"
        doc = {"notification_id": str(uuid.uuid4()), "user_id": user_id, "kind": kind, "title": title,
               "body": body, "link": link, "source_event_id": source_event_id, "dedupe_key": dedupe_key,
               "idem_keys": [idem], "count": 1, "created_at": now, "updated_at": now, "read_at": None,
               "thread_id": thread_id, "reply_id": reply_id, "test": test}
        prefs = await _prefs(safe, user_id)
        doc["channels_state"] = {c.name: await c.deliver(doc, prefs) for c in CHANNELS}
        try:
            await safe.notifications.insert_one(doc)
        except DuplicateKeyError:
            return "duplicate"
    return "created"


BROKER_REVIEWED = dict(kind="broker_reviewed", to=("client", None), title="Your broker has reviewed your request",
                       many="Your broker has reviewed your request",
                       body="Your broker will contact you about the next step.", link="/c",
                       once="broker_reviewed:{envelope_id}")


async def on_event(store, tenant_id: str, type_: str, payload: dict, event_id: str) -> None:
    rule = RULES.get(type_)
    if not rule:
        return
    p = payload or {}
    safe = wrap(store)
    if type_ == "OperatorApproval" and p.get("envelope_id"):
        # Only a priced change waits on the client; a broker taking a parked review is a different alert.
        async with use_tenant(tenant_id):
            env_types = {e["type"] async for e in safe.events.find({"payload.envelope_id": p["envelope_id"]}, {"type": 1})}
        if "EndorsementQuoteReady" not in env_types:
            if "RequestNeedsHumanReview" not in env_types:
                return
            rule = BROKER_REVIEWED
    async with use_tenant(tenant_id):
        client_id = await _client_id(safe, p)
        client = await safe.clients.find_one({"client_id": client_id}) if client_id else None
        who, need = rule["to"]
        recipients = await (_client_staff(safe, client_id) if who == "staff" and client_id else
                            _staff(safe, need) if who == "staff" else _members(safe, client_id, need))
        thread_id = p.get("thread_id") or (p.get("envelope_id") if type_ == "ClientQuestionAsked" else None)
        test = bool(p.get("test")) or bool((client or {}).get("test"))
        if thread_id and not test:
            q = await safe.events.find_one({"type": "ClientQuestionAsked", "payload.envelope_id": thread_id})
            test = bool(((q or {}).get("payload") or {}).get("test"))
        author = None
        if p.get("author_user_id"):
            author = (await safe.users.find_one({"user_id": p["author_user_id"]}) or {}).get("display_name")
    received = p.get("received_at") or datetime.now(timezone.utc).isoformat()
    policy_id = p.get("policy_id")
    if not policy_id and p.get("envelope_id"):
        async with use_tenant(tenant_id):
            row = await safe.events.find_one({"payload.envelope_id": p["envelope_id"], "payload.policy_id": {"$exists": True}})
        policy_id = ((row or {}).get("payload") or {}).get("policy_id")
    ctx = {"client": (client or {}).get("display_name", "A client"), "author": author or "Your broker",
           "policy_id": policy_id or "",
           "envelope_id": p.get("envelope_id") or "", "thread_id": thread_id or "",
           "reply_by": inbox.fmt_short(inbox.reply_by_for(inbox._utc(received)))}
    ref = thread_id or p.get("envelope_id") or event_id
    for uid in recipients:
        await deliver(store, tenant_id, uid, kind=rule["kind"], title=rule["title"].format(**ctx),
                      many=rule["many"].replace("{client}", ctx["client"]), body=rule["body"].format(**ctx),
                      link=rule["link"].format(**ctx), dedupe_key=f"{rule['kind']}:{ref}",
                      idem=rule["once"].format(**ctx) if rule.get("once") else f"ev:{event_id}",
                      source_event_id=event_id, thread_id=thread_id, reply_id=p.get("reply_id"),
                      test=test)


async def run_checks(store, tenant_id: str, now: datetime | None = None) -> dict:
    """Promise due soon / overdue, renewal 30 days out. Each alerts once per state."""
    now = now or datetime.now(timezone.utc)
    safe, made = wrap(store), {"promise_due": 0, "promise_overdue": 0, "renewal_30": 0}
    async with use_tenant(tenant_id):
        staff_for = {c["client_id"]: await _client_staff(safe, c["client_id"]) async for c in safe.clients.find({}, {"client_id": 1})}
        everyone = await _client_staff(safe, None)
        tids = [c["client_id"] async for c in safe.clients.find({"test": True}, {"client_id": 1})]
        pols = [p async for p in safe.policies.find({"status": "managed", "test": {"$ne": True}, "client_id": {"$nin": tids},
                                                     "expiry": {"$gte": now, "$lte": now + timedelta(days=30)}})]
        names = {c["client_id"]: c["display_name"] async for c in safe.clients.find({})}
    for t in await inbox.load_threads(store, tenant_id, include_tests=True):
        if t["answered"] or not t["reply_by"]:
            continue
        to = staff_for.get(t.get("client_id")) or everyone
        if now > t["reply_by"]:
            kind, title = "promise_overdue", f"Reply overdue for {t['client']}"
        elif t["reply_by"] - now <= timedelta(hours=2):
            kind, title = "promise_due", f"Reply due soon for {t['client']}"
        else:
            continue
        for uid in to:
            r = await deliver(store, tenant_id, uid, kind=kind, title=title, many=title,
                              body=f"Reply promised by {inbox.fmt_short(t['reply_by'])}.",
                              link=f"/o/inbox/{t['thread_id']}", dedupe_key=f"{kind}:{t['thread_id']}",
                              idem=f"{kind}:{t['thread_id']}", thread_id=t["thread_id"], test=t["test"])
            made[kind] += r == "created"
    for p in pols:
        exp = inbox._utc(p["expiry"])
        async with use_tenant(tenant_id):
            members = await _members(safe, p["client_id"], None)
        ae = staff_for.get(p["client_id"]) or everyone
        for uid in members + ae:
            staff = uid in ae
            r = await deliver(store, tenant_id, uid, kind="renewal_30",
                              title=f"Renewal coming up for {names.get(p['client_id'], 'a client')}" if staff else "A policy renews soon",
                              many="Renewal coming up", body=f"{p.get('product', 'A policy')} renews on {exp.astimezone(inbox.SYD):%d/%m/%Y}.",
                              link="/o/renewals" if staff else "/c/cover", dedupe_key=f"renewal_30:{p['policy_id']}",
                              idem=f"renewal_30:{p['policy_id']}:{exp.date().isoformat()}")
            made["renewal_30"] += r == "created"
    return made


async def ensure_indexes(store) -> None:
    # tenant-safe: system-only — index DDL; uniqueness makes (source event | check state) + recipient idempotent
    await store.notifications.create_index([("tenant_id", 1), ("user_id", 1), ("idem_keys", 1)], unique=True)
    await store.notifications.create_index([("tenant_id", 1), ("user_id", 1), ("updated_at", -1)])
    await store.notification_prefs.create_index([("tenant_id", 1), ("user_id", 1)], unique=True)


async def loop(store) -> None:
    await ensure_indexes(store)
    while True:
        try:
            async for t in store.tenants.find({}, {"tenant_id": 1}):  # tenant-safe: system-only
                await run_checks(store, t["tenant_id"])
        except Exception as e:  # noqa: BLE001
            log.warning("notify checks failed (non-fatal): %r", e)
        await asyncio.sleep(CHECK_EVERY_S)
