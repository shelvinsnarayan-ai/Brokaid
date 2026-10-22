"""R1.8 — claims management: lodgement (FNOL), triage flags, info requests, insurer submission, lifecycle, decision, settlement.

Never decides cover. Coverage issues are flags for the AE. Every stage is a ClaimStageChanged ledger event.
"""
from __future__ import annotations

import hashlib
import io
import re
import uuid
import zipfile
from datetime import date, datetime, timedelta, timezone

from app.db.tenant_safe import wrap
from app.kernel import bankfeed, claims_scene, holidays, inbox, notify, object_store
from app.kernel.agents.claims_live import summary_fails
from app.kernel.policy_field_guard import UnmatchedPolicyField, validate_requested_changes
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch
from app.kernel.trail_mongo import DuplicateIdempotencyKey

CLASSES = {"motor": ["accident", "theft", "glass", "hail"], "property": ["damage"], "liability": ["third_party"], "other": ["other"]}
STAGES = ["lodged", "triage", "info_requested", "submitted", "assessing", "decision", "settlement", "closed", "withdrawn"]
TERMINAL = {"closed", "withdrawn"}
NEXT = {"lodged": {"triage"}, "triage": {"info_requested", "submitted"}, "info_requested": {"triage", "submitted"},
        "submitted": {"assessing", "decision"}, "assessing": {"decision"}, "decision": {"settlement", "closed"}, "settlement": {"closed"}}
LABEL = {"lodged": "Lodged", "triage": "Triage", "info_requested": "Info requested", "submitted": "Submitted to insurer",
         "assessing": "Assessing", "decision": "Decision", "settlement": "Settlement", "closed": "Closed", "withdrawn": "Withdrawn"}
CLIENT_TEXT = {
    "lodged": "Received — your broker will review your claim.",
    "triage": "Your broker is reviewing your claim.",
    "info_requested": "Your broker needs a little more from you.",
    "submitted": "Your broker has lodged your claim with the insurer.",
    "assessing": "The insurer is assessing your claim.",
    "decision": "The insurer has made a decision on your claim.",
    "settlement": "Your claim is being settled.",
    "closed": "Your claim is closed.",
    "withdrawn": "Your claim was withdrawn.",
}
STALE_BD = {"triage": 2, "submitted": 10, "assessing": 10, "decision": 5, "settlement": 10}
OUTCOMES = {"accepted": "Accepted", "partial": "Partially accepted", "declined": "Declined"}
PAYEES = ("client", "repairer", "third_party")
FILE_KINDS = ("photo", "quote", "invoice", "police_report", "decision_letter", "info_response", "other")  # + "video" via claim threads
MAX_FILE = 10 * 1024 * 1024
FILE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/heic", "image/heif", "application/pdf")
LATE_DAYS = 30
EXCLUSION_HINTS = re.compile(r"\b(unlicen[cs]ed|drunk|alcohol|drink[- ]?driv\w*|over the limit|racing|race|wear and tear|rust|"
                             r"rideshare|uber|unroadworthy|deliberate(ly)?|intentional(ly)?|gradual|mou?ld|pre-?existing)\b", re.I)


class Bad(ValueError):
    pass


def _utc(dt):
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt.replace("Z", "+00:00").replace(" ", "T"))
    return dt.replace(tzinfo=timezone.utc) if dt and dt.tzinfo is None else dt


def _now(now=None):
    return _utc(now) or datetime.now(timezone.utc)


def _day(v) -> date | None:
    if not v:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _amt(v):
    try:
        return bankfeed.dec(v) if str(v or "").strip() else None
    except Exception:  # noqa: BLE001 — bad user input
        return None


def _clean(d):
    if d:
        d.pop("_id", None)
    return d


async def _event(store, tid, typ, payload, idem, by="system", role="claims", mocked=False):
    try:
        await append_via_dispatch(store, tid, typ, actor={"kind": "human" if by != "system" else "system", "id": by, "name": role},
                                  payload=payload, idempotency_key=idem, mocked=mocked)
    except DuplicateIdempotencyKey:
        pass


async def get(store, tid, claim_id) -> dict:
    async with use_tenant(tid):
        c = await wrap(store).claims.find_one({"claim_id": claim_id, "stage": {"$exists": True}}, {"_id": 0})
    if not c:
        raise LookupError("claim_not_found")
    return c


async def _alert(store, tid, c, who, kind, title, body, idem):
    safe = wrap(store)
    async with use_tenant(tid):
        if who == "ae":
            ae = await notify.active_ae(safe, c.get("assigned_ae")) or await notify.assigned_ae(safe, c["client_id"])  # R2.6: re-resolve
            to = [ae] if ae else await notify._staff(safe, ("ae", "principal"))  # R2.6: Principal + active AEs
        elif who == "principal":
            to = await notify._staff(safe, ("principal",))
        else:
            to = await notify._members(safe, c["client_id"], None)
    link = f"/c/claims/{c['claim_id']}" if who == "client" else f"/o/claims/{c['claim_id']}"
    for uid in to:
        await notify.deliver(store, tid, uid, kind=kind, title=title, many=title, body=body, link=link,
                             dedupe_key=f"{kind}:{c['claim_id']}", idem=idem, test=bool(c.get("test")))


# ---- policy validation (flags only, never a denial) ----
def policy_check(policy: dict | None, incident_date, rego: str | None, claim_class: str) -> dict:
    flags, out = [], {"in_force": None, "asset_on_schedule": None, "excess": None}
    if not policy:
        return {**out, "flags": [{"code": "no_policy", "text": "No policy chosen — the AE needs to match this claim to a policy."}]}
    sched = policy.get("schedule") or {}
    inc = _day(incident_date)
    start, end = _day(policy.get("inception")), _day(policy.get("expiry"))
    if inc and start and end:
        out["in_force"] = start <= inc <= end and policy.get("status") in ("managed", "in_force", "lapsed", "renewed")  # R2.6
        if not out["in_force"]:
            flags.append({"code": "outside_period", "text": f"Incident date {inc:%d/%m/%Y} is outside the policy period "
                                                            f"({start:%d/%m/%Y}–{end:%d/%m/%Y}) or the policy isn't in force."})
    out["excess"] = (sched.get("excess") or {}).get("any_one_occurrence")
    if claim_class == "motor" and sched.get("vehicles") is not None:
        if rego:
            try:
                validate_requested_changes(policy, [{"field_path": "schedule.vehicles[]", "operation": "remove",
                                                     "from_value": {"rego": rego}, "to_value": None}])
                out["asset_on_schedule"] = True
                v = next((v for v in sched["vehicles"] if re.sub(r"\W", "", str(v.get("rego", ""))).upper()
                          == re.sub(r"\W", "", rego).upper()), None)
                if v and v.get("excess") is not None:
                    out["excess"] = v["excess"]
            except UnmatchedPolicyField:
                out["asset_on_schedule"] = False
                flags.append({"code": "asset_not_on_schedule", "text": f"Vehicle {rego} isn't on the policy schedule."})
        else:
            flags.append({"code": "asset_missing", "text": "No vehicle chosen yet."})
    return {**out, "flags": flags}


async def term_for(store, tid, policy: dict | None, incident_date) -> dict | None:
    """R2.6: the term of this policy line in force on the incident date (the expiring, lapsed or renewed term)."""
    inc = _day(incident_date)

    def covers(p):
        s, e = _day(p.get("inception")), _day(p.get("expiry"))
        return bool(s and e and s <= inc <= e)
    if not policy or not inc or covers(policy):
        return policy
    safe = wrap(store)
    async with use_tenant(tid):
        prev = await safe.policies.find_one({"renewed_to": policy["policy_id"]}, {"_id": 0})
        nxt = await safe.policies.find_one({"policy_id": policy["renewed_to"]}, {"_id": 0}) if policy.get("renewed_to") else None
    return next((p for p in (prev, nxt) if p and covers(p)), policy)


async def policy_status(store, tid, policy_id: str | None) -> dict | None:
    """R2.6: the claim's policy now — in force, lapsed or renewed."""
    if not policy_id:
        return None
    async with use_tenant(tid):
        p = await wrap(store).policies.find_one({"policy_id": policy_id}, {"_id": 0, "status": 1, "expiry": 1, "renewed_to": 1})
    if not p:
        return None
    exp = _day(p.get("expiry"))
    label = {"managed": "In force", "in_force": "In force", "lapsed": "Lapsed", "renewed": "Renewed"}.get(p.get("status"), p.get("status"))
    text = {"Lapsed": f"Policy lapsed on {exp:%d/%m/%Y} — cover for incidents before then is unaffected." if exp else "Policy lapsed.",
            "Renewed": f"Policy renewed (term ended {exp:%d/%m/%Y})." if exp else "Policy renewed."}.get(label, f"Policy {label.lower()}.")
    return {"status": p.get("status"), "label": label, "text": text, "renewed_to": p.get("renewed_to")}


async def linked_complaints(store, tid, claim_id: str) -> list[dict]:
    from app.kernel import complaints as cm
    async with use_tenant(tid):
        rows = [m async for m in wrap(store).complaints.find({"links.claim_id": claim_id}, {"_id": 0}).sort("received_at", 1)]
    return [{"matter_id": m["matter_id"], "stage": m["stage"], "stage_label": cm.LABEL[m["stage"]], "summary": m["summary"],
             "received_at": _iso(m["received_at"])} for m in rows]


def triage_flags(c: dict, policy: dict | None) -> list[dict]:
    i = c["incident"]
    flags = list(policy_check(policy, i.get("date"), i.get("rego"), c["claim_class"])["flags"])
    inc, lodged = _day(i.get("date")), _day(c["lodged_at"])
    if inc and lodged and (lodged - inc).days > LATE_DAYS:
        flags.append({"code": "late_notification", "text": f"Notified {(lodged - inc).days} days after the incident."})
    hits = sorted({m.group(0).lower() for m in EXCLUSION_HINTS.finditer(f"{i.get('what', '')} {i.get('where', '')}")})
    if hits:
        flags.append({"code": "possible_exclusion", "text": f"Check for a possible exclusion (mentions: {', '.join(hits)})."})
    if i.get("injuries"):
        flags.append({"code": "injuries", "text": "Someone was injured — check CTP / liability notifications."})
    if i.get("safe") is False:
        flags.append({"code": "unsafe", "text": "Client says it isn't safe / drivable — consider towing, make-safe or emergency repairs."})
    return flags


def checklist(c: dict) -> list[dict]:
    i, kinds = c["incident"], {d["kind"] for d in c.get("docs") or []}
    items = [("what", "What happened", bool(i.get("what"))), ("when", "Incident date", bool(i.get("date"))),
             ("where", "Where it happened", bool(i.get("where"))), ("policy", "Policy matched", bool(c.get("policy_id")))]
    if c["claim_class"] == "motor":
        items.append(("asset", "Vehicle on the claim", bool(i.get("rego"))))
        items.append(("drivable", "Drivable / safe answered", i.get("safe") is not None))
        if c["subtype"] in ("accident", "theft", "hail", "glass"):
            items.append(("photos", "Photos", "photo" in kinds or c["subtype"] == "theft"))
        if c["subtype"] == "accident":
            items.append(("parties", "Other party details", bool(c.get("parties"))))
        if c["subtype"] == "theft":
            items.append(("police", "Police report number", bool(i.get("police_report_no"))))
    elif c["claim_class"] == "property":
        items += [("photos", "Photos", "photo" in kinds), ("quote", "Repair quote or invoice", bool(kinds & {"quote", "invoice"}))]
    elif c["claim_class"] == "liability":
        items.append(("parties", "Third party details", bool(c.get("parties"))))
    items.append(("injuries", "Injuries answered", i.get("injuries") is not None))
    return [{"key": k, "label": label, "done": done} for k, label, done in items]


# ---- lodgement ----
async def lodge(store, tid, *, client_id: str, policy: dict | None, body: dict, by: str, role: str, channel: str,
                test: bool = False, now=None) -> dict:
    now = _now(now)
    cls = body.get("claim_class") or "other"
    if cls not in CLASSES:
        raise Bad("Choose motor, property damage, liability or other.")
    sub = body.get("subtype") or CLASSES[cls][0]
    if sub not in CLASSES[cls]:
        raise Bad(f"For {cls}, choose one of: {', '.join(CLASSES[cls])}.")
    if policy and policy.get("client_id") != client_id:
        raise LookupError("policy_not_found")
    what = (body.get("what") or body.get("description") or "").strip()
    if len(what) < 8:
        raise Bad("Tell us what happened (at least a sentence).")
    safe = wrap(store)
    async with use_tenant(tid):
        client = await safe.clients.find_one({"client_id": client_id}) or {}
        ae = await notify.assigned_ae(safe, client_id)
    state = await holidays.state_for(store, tid)
    claim_id = str(uuid.uuid4())
    ref = await claims_scene.next_claim_reference(store, tid)
    inc = {"date": body.get("incident_date"), "time": body.get("incident_time"), "where": (body.get("where") or "").strip()[:300],
           "what": what[:4000], "police_report_no": (body.get("police_report_no") or "").strip()[:60] or None,
           "injuries": body.get("injuries"), "safe": body.get("safe"), "rego": (body.get("rego") or "").strip().upper() or None}
    if cls == "motor" and not inc["rego"]:  # R2.6
        raise Bad("Enter the vehicle's rego for a motor claim.")
    c = {"claim_id": claim_id, "claim_reference": ref, "client_id": client_id, "client_name": client.get("display_name"),
         "policy_id": (policy or {}).get("policy_id"), "insurer": (policy or {}).get("insurer_display"),
         "product": (policy or {}).get("product"), "claim_class": cls, "subtype": sub, "claim_type": f"{cls}_{sub}",
         "description": what[:4000], "incident_date": inc["date"], "incident": inc,
         "parties": [p for p in (body.get("parties") or [])[:5] if any((p or {}).values())],
         "channel": channel, "lodged_by": by, "lodged_by_role": role, "lodged_at": now, "opened_at": now,
         "ack_due": inbox.reply_by_for(now, state), "assigned_ae": ae, "stage": "lodged", "status": "lodged", "stage_since": now,
         "history": [{"from": None, "to": "lodged", "at": now, "by": by}], "docs": [], "notes": [], "info_requests": [],
         "insurer_claim": None, "assessing": None, "decision": None, "settlement": None, "ai_summary": None,
         "test": bool(test or client.get("test")), "alerts": [], "resolved_at": None, "opened_by_event": None}
    term = await term_for(store, tid, policy, inc["date"])
    pc = policy_check(term, inc["date"], inc["rego"], cls)
    c["excess"] = pc["excess"]
    c["flags"] = triage_flags(c, term)
    async with use_tenant(tid):
        await safe.claims.insert_one(dict(c))
    await _event(store, tid, "ClaimLodged", {"claim_id": claim_id, "claim_reference": ref, "client_id": client_id,
                                            "policy_id": c["policy_id"], "claim_class": cls, "subtype": sub, "channel": channel,
                                            "incident_date": inc["date"], "flags": [f["code"] for f in c["flags"]],
                                            "what_sha256": hashlib.sha256(what.encode()).hexdigest(), "test": c["test"]},
                 f"claim:{claim_id}:lodged", by, role)
    await _alert(store, tid, c, "ae", "claim_lodged", f"New claim {ref}: {c['client_name'] or 'a client'}",
                 f"{cls.title()} ({sub}) — acknowledge by {inbox.fmt_short(c['ack_due'])}.", f"claim_new:{claim_id}")
    await _alert(store, tid, c, "client", "claim_received", f"Claim {ref} received",
                 f"Received — your broker will review by {inbox.fmt_short(c['ack_due'])}.", f"claim_received:{claim_id}")
    return c


# ---- stage machine ----
async def _move(store, tid, c, to, *, by, role, note=None, now=None, **sets) -> dict:
    now, frm = _now(now), c["stage"]
    if frm in TERMINAL or (to != "withdrawn" and to not in NEXT.get(frm, set())):
        raise Bad(f"Can't move a claim from {LABEL[frm]} to {LABEL[to]}.")
    n = len(c.get("history") or [])
    h = {"from": frm, "to": to, "at": now, "by": by, "note": note}
    upd = {"stage": to, "status": to, "stage_since": now, **sets}
    if to in TERMINAL:
        upd["resolved_at"] = now
    async with use_tenant(tid):
        res = await wrap(store).claims.update_one({"claim_id": c["claim_id"], "stage": frm}, {"$set": upd, "$push": {"history": h}})
    if not res.modified_count:
        raise Bad("This claim changed in the meantime. Refresh and try again.")
    c.update(upd)
    c.setdefault("history", []).append(h)
    await _event(store, tid, "ClaimStageChanged", {"claim_id": c["claim_id"], "claim_reference": c["claim_reference"],
                                                  "client_id": c["client_id"], "policy_id": c.get("policy_id"), "from": frm, "to": to,
                                                  "note": note, "test": bool(c.get("test"))},
                 f"claim:{c['claim_id']}:{n}:{to}", by, role)
    await _alert(store, tid, c, "client", f"claim_{to}", f"Claim {c['claim_reference']}: {LABEL[to].lower()}", client_text(c),
                 f"claim_client:{c['claim_id']}:{n}")
    return c


def client_text(c: dict) -> str:
    if c["stage"] == "decision" and c.get("decision"):
        return f"The insurer's decision: {OUTCOMES[c['decision']['outcome']].lower()}."
    return CLIENT_TEXT[c["stage"]]


async def acknowledge(store, tid, c, *, by, role, now=None) -> dict:
    now = _now(now)
    return await _move(store, tid, c, "triage", by=by, role=role, now=now, ack_at=now, ack_late=now > _utc(c["ack_due"]))


async def refresh_flags(store, tid, c) -> dict:
    policy = None
    if c.get("policy_id"):
        async with use_tenant(tid):
            policy = await wrap(store).policies.find_one({"policy_id": c["policy_id"]}, {"_id": 0})
    c["flags"] = triage_flags(c, await term_for(store, tid, policy, c["incident"].get("date")))
    async with use_tenant(tid):
        await wrap(store).claims.update_one({"claim_id": c["claim_id"]}, {"$set": {"flags": c["flags"]}})
    return c


async def request_info(store, tid, c, *, items: list[str], due: str, message: str, by, role, now=None) -> dict:
    items = [i.strip()[:200] for i in items if i and i.strip()][:10]
    if not items:
        raise Bad("List what you need from the client.")
    due_d = _day(due)
    if not due_d or due_d < _now(now).date():
        raise Bad("Choose a due date from today on.")
    req = {"req_id": str(uuid.uuid4()), "items": items, "message": (message or "").strip()[:1000], "due": due_d.isoformat(),
           "status": "open", "asked_at": _now(now), "asked_by": by, "reminders": [], "answer": None}
    async with use_tenant(tid):
        await wrap(store).claims.update_one({"claim_id": c["claim_id"]}, {"$push": {"info_requests": req}})
    c.setdefault("info_requests", []).append(req)
    await _event(store, tid, "ClaimInfoRequested", {"claim_id": c["claim_id"], "req_id": req["req_id"], "items": items,
                                                   "due": req["due"], "test": bool(c.get("test"))}, f"claim_info:{req['req_id']}", by, role)
    if c["stage"] == "triage":
        await _move(store, tid, c, "info_requested", by=by, role=role, now=now, note="Information requested from the client")
    else:
        await _alert(store, tid, c, "client", "claim_info_requested", f"Claim {c['claim_reference']}: your broker needs more",
                     f"Please send: {', '.join(items)} by {due_d:%d/%m/%Y}.", f"claim_info_client:{req['req_id']}")
    return c


async def answer_info(store, tid, c, req_id: str, text: str, *, by, role, now=None) -> dict:
    req = next((r for r in c.get("info_requests") or [] if r["req_id"] == req_id), None)
    if not req:
        raise LookupError("info_request_not_found")
    if req["status"] != "open":
        raise Bad("That request has already been answered.")
    ans = {"text": (text or "").strip()[:4000], "at": _now(now), "by": by}
    async with use_tenant(tid):
        await wrap(store).claims.update_one({"claim_id": c["claim_id"], "info_requests.req_id": req_id},
                                            {"$set": {"info_requests.$.status": "answered", "info_requests.$.answer": ans}})
    req.update(status="answered", answer=ans)
    await _event(store, tid, "ClaimInfoProvided", {"claim_id": c["claim_id"], "req_id": req_id, "test": bool(c.get("test")),
                                                  "answer_sha256": hashlib.sha256(ans["text"].encode()).hexdigest()},
                 f"claim_info_answer:{req_id}", by, role)
    await _alert(store, tid, c, "ae", "claim_info_provided", f"Claim {c['claim_reference']}: client answered",
                 "The client answered your information request.", f"claim_info_answered:{req_id}")
    if c["stage"] == "info_requested" and not any(r["status"] == "open" for r in c["info_requests"]):
        await _move(store, tid, c, "triage", by=by, role=role, now=now, note="Client provided the information")
    return c


def strip_location(data: bytes, content_type: str) -> bytes:
    """Re-encode photos (incl. HEIC, R3.5) without EXIF/XMP and verify GPS is gone; PDFs unchanged. Fails closed."""
    if not content_type.startswith("image/"):
        return data
    from app.kernel import media
    try:
        return media.strip_photo(data, "image/heic" if content_type == "image/heif" else content_type)
    except media.Rejected as e:
        raise Bad(str(e)) from e


async def add_file(store, tid, c, *, kind: str, data: bytes, filename: str, content_type: str, keep_location: bool,
                   by: str, role: str, client_visible: bool = True, req_id: str | None = None) -> dict:
    if kind not in FILE_KINDS:
        raise Bad(f"File type must be one of: {', '.join(FILE_KINDS)}.")
    if not data or len(data) > MAX_FILE:
        raise Bad("Upload a file up to 10 MB.")
    if content_type not in FILE_TYPES:
        raise Bad("Upload a photo (JPEG, PNG, WebP, HEIC) or a PDF.")
    from app.kernel import filetype
    if not filetype.matches(data, content_type):
        raise Bad("The file's contents don't match its type. Upload a real photo or PDF.")
    if c["stage"] in TERMINAL:
        raise Bad("This claim is closed.")
    if not keep_location:
        data = strip_location(data, content_type)
    sha = hashlib.sha256(data).hexdigest()
    safe_name = re.sub(r"[^\w.-]", "_", filename)[-60:]
    path = f"{object_store.APP_NAME}/claims/{tid}/{c['claim_id']}/{uuid.uuid4()}-{safe_name}"
    await object_store.put_object(path, data, content_type)
    preview_path = None
    if content_type in ("image/heic", "image/heif"):  # R3.5: browsers can't show HEIC; keep a stripped JPEG preview
        from app.kernel import media
        preview_path = f"{path}.preview.jpg"
        await object_store.put_object(preview_path, media.jpeg_preview(data), "image/jpeg")
    doc = {"file_id": str(uuid.uuid4()), "kind": kind, "filename": filename[-120:], "content_type": content_type, "size": len(data),
           "sha256": sha, "storage_path": path, "uploaded_by": by, "uploaded_role": role, "uploaded_at": _now(),
           "location_kept": bool(keep_location), "client_visible": bool(client_visible), "req_id": req_id, "preview_path": preview_path}
    async with use_tenant(tid):
        await wrap(store).claims.update_one({"claim_id": c["claim_id"]}, {"$push": {"docs": doc}})
    c.setdefault("docs", []).append(doc)
    await _event(store, tid, "ClaimDocumentAdded", {"claim_id": c["claim_id"], "file_id": doc["file_id"], "kind": kind, "sha256": sha,
                                                   "size": doc["size"], "location_kept": doc["location_kept"], "test": bool(c.get("test"))},
                 f"claim_doc:{doc['file_id']}", by, role)
    return doc


async def add_note(store, tid, c, *, text: str, client_visible: bool, by: str, by_name: str, role: str) -> dict:
    note = {"note_id": str(uuid.uuid4()), "text": text.strip()[:4000], "client_visible": client_visible, "by": by, "by_name": by_name,
            "at": _now()}
    async with use_tenant(tid):
        await wrap(store).claims.update_one({"claim_id": c["claim_id"]}, {"$push": {"notes": note}})
    await _event(store, tid, "ClaimNoteAdded", {"claim_id": c["claim_id"], "note_id": note["note_id"], "client_visible": client_visible,
                                               "text_sha256": hashlib.sha256(note["text"].encode()).hexdigest(),
                                               "test": bool(c.get("test"))}, f"claim_note:{note['note_id']}", by, role)
    if client_visible:
        await _alert(store, tid, c, "client", "claim_message", f"Claim {c['claim_reference']}: message from your broker",
                     note["text"][:140], f"claim_note_client:{note['note_id']}")
    return note


# ---- insurer submission ----
def pack_pdf(c: dict, policy: dict | None) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)
    y = A4[1] - 56

    def line(t, bold=False, size=10):
        nonlocal y
        if y < 60:
            pdf.showPage()
            y = A4[1] - 56
        pdf.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        pdf.drawString(48, y, str(t)[:110])
        y -= size + 6

    i = c["incident"]
    line(f"Claim pack — {c['claim_reference']}", True, 15)
    line(f"Client: {c.get('client_name') or ''}    Lodged: {_utc(c['lodged_at']):%d/%m/%Y} ({c['channel']})")
    line("Policy", True, 12)
    p = policy or {}
    line(f"{p.get('product') or '—'} · insurer {p.get('insurer_display') or '—'} · policy {p.get('policy_id') or '—'}")
    line(f"Period {_day(p.get('inception')) or '—'} to {_day(p.get('expiry')) or '—'} · excess {c.get('excess') or '—'}")
    line("Incident", True, 12)
    line(f"Type: {c['claim_class']} / {c['subtype']} · date {i.get('date') or '—'} {i.get('time') or ''} · where {i.get('where') or '—'}")
    line(f"Vehicle: {i.get('rego') or '—'} · police report {i.get('police_report_no') or '—'} · injuries "
         f"{'yes' if i.get('injuries') else 'no' if i.get('injuries') is False else '—'} · safe/drivable "
         f"{'yes' if i.get('safe') else 'no' if i.get('safe') is False else '—'}")
    for chunk in re.findall(r".{1,100}(?:\s|$)", i.get("what") or ""):
        line(chunk.strip())
    if c.get("parties"):
        line("Other parties", True, 12)
        for pt in c["parties"]:
            line(f"{pt.get('name') or '—'} · rego {pt.get('rego') or '—'} · insurer {pt.get('insurer') or '—'}")
    line("Attachments", True, 12)
    for d in c.get("docs") or []:
        line(f"{d['kind']}: {d['filename']} ({d['size']} bytes, sha256 {d['sha256'][:16]}…)")
    if not c.get("docs"):
        line("None")
    line("Prepared by the broker for the insurer. This pack is not a coverage decision.", size=8)
    pdf.save()
    return buf.getvalue()


async def build_pack(store, tid, c, *, by, role) -> dict:
    policy = None
    if c.get("policy_id"):
        async with use_tenant(tid):
            policy = await wrap(store).policies.find_one({"policy_id": c["policy_id"]}, {"_id": 0})
    docs = [d for d in c.get("docs") or [] if not d.get("test")]  # test-flagged files never go to an insurer
    pdf = pack_pdf({**c, "docs": docs}, policy)
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{c['claim_reference']}-claim-pack.pdf", pdf)
        for d in docs:
            data, _ = await object_store.get_object(d["storage_path"])
            z.writestr(f"{d['kind']}/{d['file_id'][:8]}-{d['filename']}", data)
    base = f"{object_store.APP_NAME}/claims/{tid}/{c['claim_id']}/pack-{uuid.uuid4()}"
    await object_store.put_object(f"{base}.pdf", pdf, "application/pdf")
    await object_store.put_object(f"{base}.zip", zbuf.getvalue(), "application/zip")
    pack = {"pdf_path": f"{base}.pdf", "zip_path": f"{base}.zip", "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
            "zip_sha256": hashlib.sha256(zbuf.getvalue()).hexdigest(), "files": len(docs), "at": _now(), "by": by}
    async with use_tenant(tid):
        await wrap(store).claims.update_one({"claim_id": c["claim_id"]}, {"$set": {"pack": pack}})
    c["pack"] = pack
    await _event(store, tid, "ClaimPackGenerated", {"claim_id": c["claim_id"], "pdf_sha256": pack["pdf_sha256"],
                                                   "zip_sha256": pack["zip_sha256"], "files": pack["files"], "test": bool(c.get("test"))},
                 f"claim_pack:{base}", by, role)
    return pack


async def record_submission(store, tid, c, *, insurer_claim_no: str, lodged_on: str, contact: str, by, role, now=None) -> dict:
    no = (insurer_claim_no or "").strip()
    if not no or not _day(lodged_on):
        raise Bad("Enter the insurer's claim number and the date you lodged it.")
    ic = {"claim_no": no[:60], "lodged_on": _day(lodged_on).isoformat(), "contact": (contact or "").strip()[:200], "mocked": False,
          "recorded_by": by}
    await _event(store, tid, "ClaimSubmittedToInsurer", {"claim_id": c["claim_id"], **ic, "test": bool(c.get("test"))},
                 f"claim_submitted:{c['claim_id']}", by, role)
    return await _move(store, tid, c, "submitted", by=by, role=role, now=now, note=f"Insurer claim no. {ic['claim_no']}", insurer_claim=ic)


async def submit_mocked(store, tid, c, *, by, role, now=None) -> dict:
    if await holidays.quoting_mode(store, tid) != "mocked_allowed":
        raise Bad("This brokerage lodges claims with the insurer manually. Record the insurer's claim number instead.")
    if c["stage"] not in ("triage", "info_requested"):
        raise Bad(f"Can't submit a claim at {LABEL[c['stage']]}.")
    from app.kernel.mocks.insurer_lodgement import lodge_claim
    ack = await lodge_claim({"claim_id": c["claim_id"]})
    ic = {"claim_no": ack["insurer_reference"], "lodged_on": _now(now).date().isoformat(), "contact": f"{ack['insurer_display']} (MOCKED)",
          "mocked": True, "recorded_by": by}
    await _event(store, tid, "ClaimSubmittedToInsurer", {"claim_id": c["claim_id"], **ic, "test": bool(c.get("test"))},
                 f"claim_submitted:{c['claim_id']}", by, role, mocked=True)
    return await _move(store, tid, c, "submitted", by=by, role=role, now=now, note=f"MOCKED insurer ack {ic['claim_no']}", insurer_claim=ic)


async def set_assessing(store, tid, c, *, assessor: str, repairer: str, appointments: list[dict], by, role, now=None) -> dict:
    a = {"assessor": (assessor or "").strip()[:200], "repairer": (repairer or "").strip()[:200],
         "appointments": [{"when": (x.get("when") or "")[:40], "what": (x.get("what") or "")[:200]} for x in appointments[:10]]}
    if c["stage"] == "assessing":
        async with use_tenant(tid):
            await wrap(store).claims.update_one({"claim_id": c["claim_id"]}, {"$set": {"assessing": a}})
        c["assessing"] = a
        await _event(store, tid, "ClaimAssessmentUpdated", {"claim_id": c["claim_id"], **a}, f"claim_assess:{uuid.uuid4()}", by, role)
        return c
    return await _move(store, tid, c, "assessing", by=by, role=role, now=now, assessing=a)


async def record_decision(store, tid, c, *, outcome: str, reason: str, letter_file_id: str, amount: str | None, by, role,
                          now=None) -> dict:
    if outcome not in OUTCOMES:
        raise Bad("Choose accepted, partially accepted or declined.")
    if not any(d["file_id"] == letter_file_id and d["kind"] == "decision_letter" for d in c.get("docs") or []):
        raise Bad("Upload the insurer's decision letter first.")
    d = {"outcome": outcome, "reason": (reason or "").strip()[:2000], "letter_file_id": letter_file_id,
         "amount": str(_amt(amount)) if _amt(amount) is not None else None, "at": _now(now), "by": by, "disputed": None}
    await _event(store, tid, "ClaimDecisionRecorded", {"claim_id": c["claim_id"], "outcome": outcome, "amount": d["amount"],
                                                      "letter_file_id": letter_file_id, "test": bool(c.get("test"))},
                 f"claim_decision:{c['claim_id']}", by, role)
    return await _move(store, tid, c, "decision", by=by, role=role, now=now, note=OUTCOMES[outcome], decision=d)


async def settle(store, tid, c, *, amount: str, excess_applied: str, payee: str, payee_name: str, method: str,
                 txn_id: str | None, by, role, now=None) -> dict:
    if not c.get("decision") or c["decision"]["outcome"] == "declined":
        raise Bad("Only an accepted or partially accepted claim can be settled.")
    if payee not in PAYEES:
        raise Bad("Choose the payee: client, repairer or third party.")
    if method not in ("trust", "insurer_direct"):
        raise Bad("Choose paid via the trust account or paid directly by the insurer.")
    amt, exc = _amt(amount), _amt(excess_applied or "0")
    if amt is None or amt <= 0 or exc is None or exc < 0:
        raise Bad("Enter the settlement amount and the excess applied.")
    s = {"amount": str(amt), "excess_applied": str(exc), "payee": payee, "payee_name": (payee_name or "").strip()[:200],
         "method": method, "trust_entry_id": None, "at": _now(now), "by": by}
    if method == "trust":
        e = await bankfeed.record_payment(store, tid, by=by, role=role, kind="claim_payment", amount=str(amt), txn_id=txn_id,
                                          client_id=c["client_id"], policy_id=c.get("policy_id"),
                                          note=f"Claim {c['claim_reference']} settlement to {payee}")
        s["trust_entry_id"] = e["entry_id"]
    await _event(store, tid, "ClaimSettled", {"claim_id": c["claim_id"], **{k: s[k] for k in ("amount", "excess_applied", "payee",
                                              "method", "trust_entry_id")}, "test": bool(c.get("test"))},
                 f"claim_settled:{c['claim_id']}", by, role)
    return await _move(store, tid, c, "settlement", by=by, role=role, now=now,
                       note="Paid via trust account" if method == "trust" else "Paid directly by insurer", settlement=s)


async def close(store, tid, c, *, note: str | None, by, role, now=None) -> dict:
    return await _move(store, tid, c, "closed", by=by, role=role, now=now, note=note)


async def withdraw(store, tid, c, *, reason: str, by, role, now=None) -> dict:
    if not (reason or "").strip():
        raise Bad("Give a reason for withdrawing the claim.")
    return await _move(store, tid, c, "withdrawn", by=by, role=role, now=now, note=reason.strip()[:500])


async def dispute(store, tid, c, *, reason: str, by, role) -> dict:
    d = c.get("decision") or {}
    if d.get("outcome") not in ("declined", "partial"):
        raise Bad("Only a declined or partly accepted claim can be disputed.")
    if d.get("disputed"):
        raise Bad("You've already told your broker you disagree.")
    disp = {"reason": (reason or "").strip()[:2000], "at": _now(), "by": by}
    async with use_tenant(tid):
        await wrap(store).claims.update_one({"claim_id": c["claim_id"]}, {"$set": {"decision.disputed": disp}})
    d["disputed"] = disp
    await _event(store, tid, "ClaimDecisionDisputed", {"claim_id": c["claim_id"], "test": bool(c.get("test")),
                                                      "reason_sha256": hashlib.sha256(disp["reason"].encode()).hexdigest()},
                 f"claim_dispute:{c['claim_id']}", by, role)
    for who in ("ae", "principal"):
        await _alert(store, tid, c, who, "claim_disputed", f"Client disputes claim {c['claim_reference']}",
                     "The client disagrees with the insurer's decision — consider logging an IDR complaint in Compliance.",
                     f"claim_dispute_alert:{c['claim_id']}")
    return c


# ---- AI summary (no coverage decision) ----
async def ai_summary(store, tid, c, *, by, role) -> dict:
    from app.kernel.brain import Brain
    from app.kernel.contracts.models import Actor, ContextEnvelope, InvocationBudget
    i = c["incident"]
    text = (f"Claim class: {c['claim_class']} / {c['subtype']}\nIncident date: {i.get('date') or 'not given'}\n"
            f"Where: {i.get('where') or 'not given'}\nWhat happened (client's words): {i.get('what')}\n"
            f"Injuries: {i.get('injuries')}\nSafe/drivable: {i.get('safe')}\nPolice report no.: {i.get('police_report_no') or 'none'}\n"
            f"Other parties: {c.get('parties') or 'none'}")
    env = ContextEnvelope(envelope_id=uuid.uuid4(), tenant_id=uuid.UUID(tid), matter_id=None, client_id=uuid.UUID(c["client_id"]),
                          class_key="claim_admin", intent="summarise claim incident", inputs={"mode": "summary", "incident_text": text},
                          context_events=[], risk_schema_refs=[], tenant_config={},
                          budget=InvocationBudget(max_input_tokens=3000, max_output_tokens=600, max_wall_ms=45000),
                          caller=Actor(kind="human", id=by, name=role), ts=datetime.now(timezone.utc))
    out = await Brain(store).invoke(env, "claims")
    summary = ((out.payload or {}).get("summary") or "").strip()
    fails = summary_fails(summary, text)
    s = {"text": None if fails else summary, "withheld": fails or None, "at": _now(), "by": by, "label": "AI summary"}
    async with use_tenant(tid):
        await wrap(store).claims.update_one({"claim_id": c["claim_id"]}, {"$set": {"ai_summary": s}})
    c["ai_summary"] = s
    return s


# ---- SLA tick ----
async def tick(store, tid, now=None) -> int:
    now, n = _now(now), 0
    state = await holidays.state_for(store, tid)
    async with use_tenant(tid):
        rows = [c async for c in wrap(store).claims.find({"stage": {"$exists": True, "$nin": list(TERMINAL)}}, {"_id": 0})]
    for c in rows:
        marks = []
        if c["stage"] == "lodged" and now >= _utc(c["ack_due"]):
            marks.append(("ack_overdue", ("ae", "principal"), f"Claim {c['claim_reference']} not acknowledged in 1 business day",
                          "Acknowledge the claim and start triage."))
        bd = STALE_BD.get(c["stage"])
        if bd and holidays.business_days_between(_utc(c["stage_since"]).date(), now.date(), state) >= bd:
            marks.append((f"stale:{len(c['history'])}", ("ae",), f"Claim {c['claim_reference']} stale at {LABEL[c['stage']]}",
                          f"No movement for {bd}+ business days."))
        for r in c.get("info_requests") or []:
            if r["status"] != "open":
                continue
            due = date.fromisoformat(r["due"])
            if now.date() >= due - timedelta(days=2):
                marks.append((f"info_soon:{r['req_id']}", ("client",), f"Claim {c['claim_reference']}: information due {due:%d/%m/%Y}",
                              f"Your broker still needs: {', '.join(r['items'])}."))
            if now.date() >= due:
                marks.append((f"info_due:{r['req_id']}", ("client", "ae"), f"Claim {c['claim_reference']}: information overdue",
                              f"Still needed: {', '.join(r['items'])}."))
        sent = set(c.get("alerts") or [])
        for key, whos, title, body in marks:
            if key in sent:
                continue
            for who in whos:
                await _alert(store, tid, c, who, "claim_sla", title, body, f"claim_sla:{c['claim_id']}:{key}:{who}")
            async with use_tenant(tid):
                await wrap(store).claims.update_one({"claim_id": c["claim_id"]}, {"$addToSet": {"alerts": key}})
            n += 1
    return n


# ---- views ----
def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def overdue(c: dict, now: datetime) -> bool:
    return (c["stage"] == "lodged" and now >= _utc(c["ack_due"])) or any(
        r["status"] == "open" and now.date() >= date.fromisoformat(r["due"]) for r in c.get("info_requests") or [])


def stale(c: dict, now: datetime, state: str | None = None) -> bool:
    bd = STALE_BD.get(c["stage"])
    return bool(bd) and holidays.business_days_between(_utc(c["stage_since"]).date(), now.date(), state) >= bd


def row(c: dict, now: datetime) -> dict:
    return {"claim_id": c["claim_id"], "claim_reference": c["claim_reference"], "client_id": c["client_id"],
            "client_name": c.get("client_name"), "claim_class": c["claim_class"], "subtype": c["subtype"], "stage": c["stage"],
            "stage_label": LABEL[c["stage"]], "insurer": c.get("insurer"), "assigned_ae": c.get("assigned_ae"),
            "lodged_at": _iso(c["lodged_at"]), "age_days": (now - _utc(c["lodged_at"])).days, "overdue": overdue(c, now),
            "flags": [f["code"] for f in c.get("flags") or []], "rego": c["incident"].get("rego"), "test": bool(c.get("test"))}


def _doc_out(d):
    return {**{k: _iso(v) for k, v in d.items() if k not in ("storage_path", "preview_path")}, "has_preview": bool(d.get("preview_path"))}


def staff_view(c: dict, now: datetime) -> dict:
    out = {k: _iso(v) for k, v in c.items() if k not in ("docs", "history", "notes", "info_requests", "pack")}  # "docs" → "files" out
    out.update(row(c, now))
    out["checklist"] = checklist(c)
    out["files"] = [_doc_out(d) for d in c.get("docs") or [] if not d.get("test")]
    out["notes"] = [{**n, "at": _iso(n["at"])} for n in c.get("notes") or []]
    out["info_requests"] = [{**r, "asked_at": _iso(r["asked_at"]),
                             "answer": r["answer"] and {**r["answer"], "at": _iso(r["answer"]["at"])}} for r in c.get("info_requests") or []]
    out["timeline"] = [{"from": h["from"], "to": h["to"], "label": LABEL[h["to"]], "at": _iso(h["at"]), "by": h["by"],
                        "note": h.get("note")} for h in c.get("history") or []]
    out["next"] = sorted(NEXT.get(c["stage"], set()))
    out["pack"] = c.get("pack") and {"at": _iso(c["pack"]["at"]), "files": c["pack"]["files"], "pdf_sha256": c["pack"]["pdf_sha256"]}
    for k in ("decision", "settlement", "ai_summary"):
        if out.get(k):
            out[k] = {kk: _iso(vv) for kk, vv in out[k].items()}
    return out


def client_view(c: dict, settings: dict) -> dict:
    d = c.get("decision") or {}
    options = None
    if d.get("outcome") in ("declined", "partial"):
        options = {"text": ["Ask your broker to explain the insurer's reasons — they can go back to the insurer with more information.",
                            "If you disagree, tell your broker below. You can also make a complaint to us.",
                            "If you're still not satisfied after our response, you can go to AFCA (free and independent)."],
                   "afca_text": settings.get("afca_text"), "disputed": bool(d.get("disputed"))}
    timeline = [{"label": LABEL[h["to"]], "text": CLIENT_TEXT[h["to"]], "at": _iso(h["at"])} for h in c.get("history") or []]
    timeline += [{"label": "Message from your broker", "text": n["text"], "at": _iso(n["at"])} for n in c.get("notes") or []
                 if n["client_visible"]]
    timeline.sort(key=lambda x: x["at"])
    s = c.get("settlement")
    return {"claim_id": c["claim_id"], "claim_reference": c["claim_reference"], "stage": c["stage"], "stage_label": LABEL[c["stage"]],
            "status_text": client_text(c), "claim_class": c["claim_class"], "subtype": c["subtype"], "lodged_at": _iso(c["lodged_at"]),
            "ack_due": _iso(c["ack_due"]), "incident": c["incident"], "excess": c.get("excess"), "product": c.get("product"),
            "insurer_claim_no": (c.get("insurer_claim") or {}).get("claim_no"),
            "insurer_mocked": bool((c.get("insurer_claim") or {}).get("mocked")),
            "timeline": timeline, "files": [_doc_out(x) for x in c.get("docs") or [] if x["client_visible"] and not x.get("test")],
            "info_requests": [{"req_id": r["req_id"], "items": r["items"], "message": r["message"], "due": r["due"], "status": r["status"]}
                              for r in c.get("info_requests") or []],
            "decision": d and {"outcome": d["outcome"], "label": OUTCOMES[d["outcome"]], "reason": d.get("reason"), "amount": d.get("amount")},
            "options": options,
            "settlement": s and {"amount": s["amount"], "excess_applied": s["excess_applied"], "payee": s["payee"],
                                 "method": "Paid via your broker's trust account" if s["method"] == "trust" else "Paid directly by the insurer"}}


async def weekly_metrics(safe, now: datetime) -> list[dict]:
    rows = [c async for c in safe.claims.find({"stage": {"$exists": True}, "test": {"$ne": True}}, {"_id": 0})]
    open_ = [c for c in rows if c["stage"] not in TERMINAL]
    avg = round(sum((now - _utc(c["lodged_at"])).days for c in open_) / len(open_), 1) if open_ else 0
    basis = "claims register"
    return [{"key": "claims_open", "label": "Open claims", "value": len(open_), "data_basis": basis},
            {"key": "claims_avg_days", "label": "Average days open (open claims)", "value": avg, "data_basis": basis},
            {"key": "claims_stale", "label": "Stale claims", "value": sum(stale(c, now) for c in open_), "data_basis": basis}]


async def ensure_indexes(store) -> None:
    await store.claims.create_index([("tenant_id", 1), ("stage", 1)], name="claims_tenant_stage")
