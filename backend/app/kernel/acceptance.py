"""R2.0 — click-to-accept: render the exact document, the client accepts it in-app, ledgered as ClientAcceptanceRecorded.

Not a digital signature. "Signed offline" covers insurers that need a wet signature (OfflineSignatureRecorded).
"""
from __future__ import annotations

import difflib
import hashlib
import io
import re
import uuid
from datetime import datetime, timedelta, timezone

from app.db.tenant_safe import wrap
from app.kernel import filetype, object_store
from app.kernel.holidays import SYD
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch
from app.kernel.trail_mongo import DuplicateIdempotencyKey

LABEL = "Accepted in brokAId (not a digital signature)"
OFFLINE_LABEL = "Signed offline (scan held by the broker)"
SIGNED_TYPES = ("SignatureCompleted", "ClientAcceptanceRecorded", "OfflineSignatureRecorded")
KINDS = ("endorsement", "renewal")
SCAN_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/heic"}
MAX_SCAN = 15 * 1024 * 1024


class Bad(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z' -]", "", (s or "").lower().replace("-", " ")).split())


def name_match(typed: str, on_file: str) -> str:
    """exact | close | mismatch — close tolerates case, punctuation, middle names and small typos."""
    a, b = _norm(typed), _norm(on_file)
    if not a:
        return "mismatch"
    if a == b:
        return "exact"
    ta, tb = a.split(), b.split()
    if tb and ta and ta[0] == tb[0] and ta[-1] == tb[-1]:
        return "close"
    return "close" if difflib.SequenceMatcher(None, a, b).ratio() >= 0.85 else "mismatch"


async def _lineage(safe, envelope_id: str) -> dict:
    return {e["type"]: e async for e in safe.events.find({"payload.envelope_id": envelope_id}).sort("seq", 1)}


def _money(v) -> str:
    try:
        return f"A${float(v):,.2f}"
    except (TypeError, ValueError):
        return str(v) if v not in (None, "") else "—"


async def subject(store, tid: str, kind: str, ref_id: str) -> dict:
    """What is being accepted: client, envelope, policy, title and the exact lines shown in the PDF."""
    safe = wrap(store)
    async with use_tenant(tid):
        if kind == "renewal":
            r = await safe.renewals.find_one({"renewal_id": ref_id}, {"_id": 0})
            if not r:
                raise Bad(404, "not_found")
            q = next((q for q in r.get("quotes") or [] if q["quote_id"] == (r.get("decision") or {}).get("choice")), None)
            if not q:
                raise Bad(409, "Choose a renewal option first.")
            old = await safe.policies.find_one({"policy_id": r["policy_id"]}, {"_id": 0}) or {}
            start = old.get("expiry")
            start = start.replace(tzinfo=timezone.utc) if isinstance(start, datetime) and start.tzinfo is None else start
            period = (f"{start.astimezone(SYD):%d/%m/%Y} to {(start + timedelta(days=365)).astimezone(SYD):%d/%m/%Y}"
                      if isinstance(start, datetime) else "—")
            lines = [("Client", r.get("client_name") or "—"), ("Policy", r.get("product") or "—"), ("Insurer", q["insurer"]),
                     ("Period of cover", period), ("Premium", _money(q.get("premium"))), ("Excess", _money(q.get("excess")))]
            for ch in r.get("accepted_changes") or []:
                val = ch.get("to_value") or ch.get("from_value")
                lines.append(("Schedule change", f"{ch['operation']} {ch['field_path']}: {val.get('rego') if isinstance(val, dict) else val}"))
            return {"client_id": r["client_id"], "envelope_id": r["envelope_id"], "policy_id": r["policy_id"],
                    "title": "Renewal terms", "lines": lines, "ready": r["stage"] == "paid" or (r.get("signature") is not None)}
        if kind != "endorsement":
            raise Bad(404, "not_found")
        lin = await _lineage(safe, ref_id)
        pid = next((e["payload"]["policy_id"] for e in lin.values() if (e.get("payload") or {}).get("policy_id")), None)
        if not pid:
            raise Bad(404, "not_found")
        pol = await safe.policies.find_one({"policy_id": pid}, {"_id": 0}) or {}
        client = await safe.clients.find_one({"client_id": pol.get("client_id")}, {"_id": 0, "display_name": 1}) or {}
    sub = (lin.get("SubmissionDrafted") or {}).get("payload") or {}
    changes = ((sub.get("structured_submission") or {}).get("requested_changes") or sub.get("requested_changes")
               or ((lin.get("ClientChangeRequested") or {}).get("payload") or {}).get("proposed_changes") or [])
    quote = (lin.get("CarrierQuoted") or {}).get("payload") or {}
    lines = [("Client", client.get("display_name") or "—"), ("Policy", pol.get("product") or "—"),
             ("Insurer", pol.get("insurer_display") or "—")]
    for ch in changes:
        val = ch.get("to_value") if ch.get("to_value") is not None else ch.get("from_value")
        lines.append(("Change", f"{ch.get('operation', 'change')} {ch.get('field_path', '')}: "
                                f"{val.get('rego') or val.get('description') if isinstance(val, dict) else val}"))
    if quote.get("carrier_name"):
        lines.append(("Quoted by", f"{quote['carrier_name']}" + (f" (ref {quote['insurer_reference']})" if quote.get("insurer_reference") else "")))
    prem = quote.get("premium_change_amount", quote.get("premium_delta"))
    if prem is not None:
        lines.append(("Premium change", _money(prem)))
    if quote.get("premium_new_annual") is not None:
        lines.append(("New annual premium", _money(quote["premium_new_annual"])))
    for t in (quote.get("conditions") or [])[:6]:
        lines.append(("Condition", str(t)))
    if quote.get("effective_date"):
        lines.append(("Effective", str(quote["effective_date"])[:10]))
    return {"client_id": pol.get("client_id"), "envelope_id": ref_id, "policy_id": pid, "title": "Endorsement schedule — change summary",
            "lines": lines, "ready": "payment.advised" in lin and "Approval" in lin}


def render_pdf(title: str, lines: list[tuple[str, str]], ref: str) -> bytes:
    from xml.sax.saxutils import escape
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title=title, invariant=1)  # deterministic bytes → stable SHA-256
    st = getSampleStyleSheet()
    body = [Paragraph(escape(title), st["Title"]), Paragraph(escape(f"Reference {ref}"), st["Normal"]), Spacer(1, 10),
            Table([[k, str(v)] for k, v in lines], colWidths=[150, 330]), Spacer(1, 14),
            Paragraph(escape("By accepting in brokAId you confirm you have read these terms and accept them. "
                             f"This is recorded as: {LABEL}."), st["Normal"])]
    doc.build(body)
    return buf.getvalue()


async def document(store, tid: str, kind: str, ref_id: str) -> dict:
    """Render (once per content) and store the exact document; returns the stored row incl. sha256."""
    subj = await subject(store, tid, kind, ref_id)
    content_key = hashlib.sha256(repr((subj["title"], subj["lines"])).encode()).hexdigest()
    safe = wrap(store)
    async with use_tenant(tid):
        row = await safe.acceptance_docs.find_one({"kind": kind, "ref_id": ref_id, "content_key": content_key}, {"_id": 0})
    if row:
        return {**row, "subject": subj}
    pdf = render_pdf(subj["title"], subj["lines"], ref_id)
    sha = hashlib.sha256(pdf).hexdigest()
    path = f"tenants/{tid}/acceptance/{kind}/{ref_id}/{sha}.pdf"
    await object_store.put_object(path, pdf, "application/pdf")
    row = {"doc_id": str(uuid.uuid4()), "kind": kind, "ref_id": ref_id, "content_key": content_key, "sha256": sha,
           "storage_path": path, "title": subj["title"], "lines": [list(x) for x in subj["lines"]], "client_id": subj["client_id"],
           "envelope_id": subj["envelope_id"], "created_at": _now()}
    async with use_tenant(tid):
        await safe.acceptance_docs.insert_one(dict(row))
    row.pop("_id", None)
    return {**row, "subject": subj}


async def pdf_bytes(doc: dict) -> bytes:
    data, _ = await object_store.get_object(doc["storage_path"])
    return data


async def recorded(store, tid: str, envelope_id: str) -> dict | None:
    async with use_tenant(tid):
        return await wrap(store).acceptances.find_one({"envelope_id": envelope_id}, {"_id": 0})


async def _event(store, tid, typ, actor, payload, idem):
    async with use_tenant(tid):
        try:
            row = await append_via_dispatch(store, tid, typ, actor=actor, payload=payload, idempotency_key=idem, mocked=False)
        except DuplicateIdempotencyKey:
            row = await wrap(store).events.find_one({"idempotency_key": idem})
    return row


async def accept(store, tid: str, kind: str, ref_id: str, *, session, typed_name: str, accepted: bool, doc_sha256: str,
                 confirm_name: bool, ip: str | None, user_agent: str | None) -> dict:
    if not accepted:
        raise Bad(422, "Tick “I've read and accept” first.")
    doc = await document(store, tid, kind, ref_id)
    if not doc["subject"]["ready"]:
        raise Bad(409, "This isn't ready to accept yet.")
    prior = await recorded(store, tid, doc["envelope_id"])
    if prior:
        return prior
    if doc_sha256 != doc["sha256"]:
        raise Bad(409, "The document has changed since you opened it. Please re-open it and review it again.")
    if hashlib.sha256(await pdf_bytes(doc)).hexdigest() != doc["sha256"]:
        raise Bad(409, "The stored document failed its integrity check. Your broker has been told.")
    safe = wrap(store)
    async with use_tenant(tid):
        user = await safe.users.find_one({"user_id": session.user_id}, {"_id": 0, "display_name": 1, "email": 1}) or {}
        mem = await safe.memberships.find_one({"user_id": session.user_id, "client_id": doc["client_id"], "revoked_at": None},
                                              {"_id": 0}) or {}
    on_file = user.get("display_name") or ""
    match = name_match(typed_name, on_file)
    if match == "mismatch" and not confirm_name:
        raise Bad(409, "name_mismatch")
    now = _now()
    payload = {"kind": kind, "ref_id": ref_id, "envelope_id": doc["envelope_id"], "policy_id": doc["subject"]["policy_id"],
               "client_id": doc["client_id"], "user_id": session.user_id, "typed_name": typed_name.strip()[:120],
               "name_on_file": on_file, "name_match": match if match != "mismatch" else "confirmed_mismatch",
               "membership": {"membership_id": mem.get("membership_id"), "role": mem.get("role"),
                              "authority": {k: v for k, v in (mem.get("authority") or {}).items() if k.startswith("can_")}},
               "accepted_at_utc": now.isoformat(timespec="seconds"),
               "accepted_at_sydney": now.astimezone(SYD).isoformat(timespec="seconds"),
               "ip": ip, "user_agent": (user_agent or "")[:300], "doc_sha256": doc["sha256"], "doc_id": doc["doc_id"],
               "session_id": session.session_id, "label": LABEL, "mocked": False}
    ev = await _event(store, tid, "ClientAcceptanceRecorded", {"kind": "human", "id": session.user_id, "name": session.active_role},
                      payload, f"accept:{doc['envelope_id']}")
    rec = {"acceptance_id": str(uuid.uuid4()), "method": "click_to_accept", "label": LABEL, "event_id": ev["event_id"],
           **{k: payload[k] for k in ("kind", "ref_id", "envelope_id", "policy_id", "client_id", "user_id", "typed_name",
                                      "doc_sha256", "doc_id", "accepted_at_utc", "accepted_at_sydney")}, "created_at": now}
    async with use_tenant(tid):
        await safe.acceptances.insert_one(dict(rec))
    rec.pop("_id", None)
    async with use_tenant(tid):
        client = await safe.clients.find_one({"client_id": doc["client_id"]}, {"test": 1}) or {}
    await _receipt(store, tid, session.user_id, rec, test=bool(client.get("test")))
    return rec


async def _receipt(store, tid, user_id, rec, *, test: bool):
    from app.kernel import notify
    link = f"/c/acceptance/{rec['kind']}/{rec['ref_id']}"
    await notify.deliver(store, tid, user_id, kind="acceptance_receipt", title="Your acceptance is recorded", many="{n} acceptances recorded",
                         body=f"{LABEL}. Document SHA-256 {rec['doc_sha256'][:16]}… — download it any time from the link.",
                         link=link, dedupe_key=f"accept:{rec['envelope_id']}", idem=f"accept-receipt:{rec['envelope_id']}",
                         source_event_id=rec["event_id"], test=test)


async def offline(store, tid: str, kind: str, ref_id: str, *, data: bytes, filename: str, reason: str, by: str, role: str) -> dict:
    if not (reason or "").strip():
        raise Bad(422, "Say why this was signed offline (e.g. the insurer needs a wet signature).")
    if len(data) > MAX_SCAN:
        raise Bad(413, "The scan is too large (15 MB max).")
    mime = filetype.sniff(data)
    if mime not in SCAN_TYPES:
        raise Bad(422, "Upload the signed scan as a PDF, JPEG, PNG or HEIC.")
    doc = await document(store, tid, kind, ref_id)
    if not doc["subject"]["ready"]:
        raise Bad(409, "This isn't ready to accept yet.")
    prior = await recorded(store, tid, doc["envelope_id"])
    if prior:
        return prior
    sha = hashlib.sha256(data).hexdigest()
    path = f"tenants/{tid}/acceptance/{kind}/{ref_id}/offline-{sha}"
    await object_store.put_object(path, data, mime)
    now = _now()
    payload = {"kind": kind, "ref_id": ref_id, "envelope_id": doc["envelope_id"], "policy_id": doc["subject"]["policy_id"],
               "client_id": doc["client_id"], "scan_sha256": sha, "scan_type": mime, "filename": (filename or "scan")[:120],
               "reason": reason.strip()[:500], "doc_sha256": doc["sha256"], "recorded_by": by, "label": OFFLINE_LABEL,
               "recorded_at_utc": now.isoformat(timespec="seconds"), "mocked": False}
    ev = await _event(store, tid, "OfflineSignatureRecorded", {"kind": "human", "id": by, "name": role}, payload,
                      f"accept:{doc['envelope_id']}")
    rec = {"acceptance_id": str(uuid.uuid4()), "method": "offline", "label": OFFLINE_LABEL, "event_id": ev["event_id"],
           **{k: payload[k] for k in ("kind", "ref_id", "envelope_id", "policy_id", "client_id", "doc_sha256", "scan_sha256",
                                      "reason")}, "scan_path": path, "user_id": by, "created_at": now,
           "accepted_at_utc": payload["recorded_at_utc"]}
    async with use_tenant(tid):
        await wrap(store).acceptances.insert_one(dict(rec))
    rec.pop("_id", None)
    return rec


def public(rec: dict | None) -> dict | None:
    if not rec:
        return None
    return {k: rec.get(k) for k in ("acceptance_id", "method", "label", "event_id", "typed_name", "doc_sha256", "scan_sha256",
                                    "accepted_at_utc", "accepted_at_sydney", "reason")}


async def ensure_indexes(store) -> None:
    # tenant-safe: system-only — index DDL; one acceptance per envelope
    await store.acceptances.create_index([("tenant_id", 1), ("envelope_id", 1)], unique=True)
    await store.acceptance_docs.create_index([("tenant_id", 1), ("kind", 1), ("ref_id", 1), ("content_key", 1)], unique=True)
