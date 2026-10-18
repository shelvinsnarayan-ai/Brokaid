"""GET /api/o/envelope/{envelope_id}/change-packet — R0.3.

Plain-text BMS change packet for MANUAL keying into iAdviser. Read-only:
nothing is sent to iAdviser or any BMS. Emits one `ChangePacketIssued`
ledger event per envelope (idempotent; repeat GETs re-render the same packet).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app.auth.sessions import Session
from app.db.tenant_safe import wrap
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch
from app.kernel.trail_mongo import DuplicateIdempotencyKey
from app.middleware.session import require_operator_session
from app.routes.queue import _ensure_queue_role

router = APIRouter(prefix="/api/o/envelope")
SYD = ZoneInfo("Australia/Sydney")
MOCK = "** MOCKED **"


def _aware(v: Any) -> datetime:
    if isinstance(v, str):
        v = datetime.fromisoformat(v.replace("Z", "+00:00"))
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


def _syd(v: Any, with_time: bool = True) -> str:
    d = _aware(v).astimezone(SYD)
    # Demo-literal "AEST" per Shelvin's R0.3 ruling — not DST-correct (Sydney is AEDT Oct–Apr).
    return d.strftime("%d/%m/%Y %H:%M") + " AEST" if with_time else d.strftime("%d/%m/%Y")


def _money(v: Any) -> str:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    return f"{'-' if n < 0 else ''}AUD ${abs(n):,.2f}"


def _val(v: Any) -> str:
    if v is None or v == "":
        return "(none)"
    if isinstance(v, dict):
        rego = v.get("rego") or v.get("registration")
        desc = " ".join(str(v[k]) for k in ("year", "make", "model") if v.get(k))
        if rego or desc:
            return " ".join(x for x in (rego, desc) if x)
        return ", ".join(f"{k}={x}" for k, x in v.items())
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return f"{v:,}"
    return str(v)


def _change_lines(changes: list[dict], basis: str) -> list[str]:
    tag = f"[basis: {basis}]" + (f" {MOCK}" if basis == "synthetic" else "")
    out = []
    for i, ch in enumerate(changes, 1):
        op = (ch.get("operation") or "set").lower()
        ref = ch.get("from_value")
        if ref is None and op == "remove":
            ref = ch.get("to_value")
        before = _val(ref) if op != "add" else "(none)"
        after = "(removed)" if op == "remove" else _val(ch.get("to_value"))
        out.append(f"  {i}. {ch.get('field_path')} ({op})  BEFORE: {before}  AFTER: {after}   {tag}")
    return out or ["  (no structured changes on this request)"]


async def _user_name(safe, actor: dict) -> str:
    u = await safe.users.find_one({"user_id": actor.get("id")})
    return (u or {}).get("display_name") or actor.get("name") or "unknown"


def render_packet(ctx: dict[str, Any]) -> str:
    q = ctx["quote"]
    lines = [
        "BMS CHANGE PACKET — FOR MANUAL KEYING INTO iAdviser",
        "This is not sent to iAdviser. Nothing has been written to any BMS.",
        "",
        f"Packet ID       : {ctx['packet_id']}",
        f"Request ID      : {ctx['envelope_id']}",
        f"Policy ID       : {ctx['policy_id']}     Insurer policy no: {ctx['insurer_policy_no']}",
        f"Client          : {ctx['client']}",
        f"Class           : {ctx['class']}",
        f"Advice tier     : {ctx['tier']}",
        f"Effective date  : {ctx['effective']}",
        f"Generated       : {ctx['generated']}",
        f"Data basis      : {ctx['basis']}" + (f"  {MOCK}" if ctx["basis"] == "synthetic" else ""),
        "",
        "CHANGES (key each line)",
        *_change_lines(ctx["changes"], ctx["basis"]),
        "",
        "PREMIUM  ** MOCKED — DO NOT KEY AS A REAL FIGURE **",
        f"  Change    : {_money(q.get('premium_change_amount'))} (MOCKED)",
        f"  New annual: {_money(q.get('premium_new_annual'))} (MOCKED)",
        f"  Carrier   : {q.get('carrier_name') or '—'} (MOCKED)",
        f"  Payment ref (CRN): {ctx.get('crn') or 'not issued yet'}",
        "",
        "APPROVALS",
        f"  Broker approved : {ctx['broker']}",
        f"  Client approved : {ctx['client_approval']}",
        "",
        "KEYED INTO BMS BY: ______________  DATE: __/__/____  BMS REF: __________",
        "",
    ]
    return "\n".join(lines)


@router.get("/{envelope_id}/change-packet", response_class=PlainTextResponse)
async def change_packet(
    envelope_id: str, request: Request, probe: bool = False,
    session: Session = Depends(require_operator_session),
) -> PlainTextResponse:
    """`?probe=1` reports readiness only (no ledger event)."""
    _ensure_queue_role(session)
    store = request.app.state.mongo_db
    tid = session.tenant_id
    safe = wrap(store)
    async with use_tenant(tid):
        evs = [e async for e in safe.events.find({"payload.envelope_id": envelope_id}).sort("seq", 1)]
        by: dict[str, dict] = {}
        for e in evs:
            by[e["type"]] = e
        signed = next((by[t] for t in ("ClientAcceptanceRecorded", "OfflineSignatureRecorded", "SignatureCompleted") if t in by), None)
        if signed is None:
            raise HTTPException(status_code=409, detail={
                "code": "ChangePacketNotReady",
                "detail": "change packet is issued once the request is signed (executing)",
            })
        if probe:
            return PlainTextResponse("ready", media_type="text/plain; charset=utf-8")
        policy_id = by["ClientChangeRequested"]["payload"]["policy_id"]
        policy = await safe.policies.find_one({"policy_id": policy_id}) or {}
        client = await safe.clients.find_one({"client_id": policy.get("client_id")}) or {}
        sub = (by.get("SubmissionDrafted") or {}).get("payload") or {}
        changes = (sub.get("structured_submission") or {}).get("requested_changes") or []
        op_ev, cl_ev = by.get("OperatorApproval"), by.get("Approval")
        broker = (f"{await _user_name(safe, op_ev['actor'])}, {op_ev['actor'].get('name')}  "
                  f"{_syd(op_ev['ts'])}  Ledger event: {op_ev['event_id']}") if op_ev else "(missing)"
        client_appr = (f"{await _user_name(safe, cl_ev['actor'])}          "
                       f"{_syd(cl_ev['ts'])}  Ledger event: {cl_ev['event_id']}") if cl_ev else "(missing)"
        tier_ev = (by.get("AdviceBoundaryClassified") or {}).get("payload") or {}
        issued = by.get("ChangePacketIssued")
        if issued:
            packet_id = issued["payload"]["packet_id"]
            generated_at = issued["payload"]["generated_at"]
        else:
            now = datetime.now(timezone.utc)
            day = now.astimezone(SYD).strftime("%Y%m%d")
            n = await safe.events.count_documents(
                {"type": "ChangePacketIssued", "payload.packet_id": {"$regex": f"^CP-{day}-"}})
            packet_id, generated_at = f"CP-{day}-{n + 1:03d}", now.isoformat(timespec="seconds")
        cert_ev = by.get("CertificateRegenerated")

    async with use_tenant(tid):
        crn = ((await safe.payables.find_one({"envelope_id": envelope_id})) or {}).get("crn")
    basis = "synthetic" if policy.get("fictional") or client.get("fictional") else "manual"
    body = render_packet({
        "packet_id": packet_id, "envelope_id": envelope_id, "policy_id": policy_id,
        "insurer_policy_no": policy.get("insurer_policy_no") or "not held",
        "client": client.get("display_name") or "—", "class": policy.get("product") or "—",
        "tier": tier_ev.get("tier") or "not classified (request pre-dates the boundary check)",
        "effective": _syd(signed["ts"], with_time=False),
        "generated": _syd(generated_at), "basis": basis, "changes": changes,
        "quote": (by.get("CarrierQuoted") or {}).get("payload") or {},
        "broker": broker, "client_approval": client_appr, "crn": crn,
    })
    link_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    key = f"changepacket:{envelope_id}"
    async with use_tenant(tid):
        if not issued:
            try:
                await append_via_dispatch(
                    store, tid, "ChangePacketIssued",
                    actor={"kind": "system", "id": "broker-ops", "name": "broker-ops"},
                    payload={"envelope_id": envelope_id, "policy_id": policy_id,
                             "packet_id": packet_id, "link_hash": link_hash,
                             "generated_at": generated_at, "mocked": True},
                    idempotency_key=key, mocked=True,
                )
            except DuplicateIdempotencyKey:
                pass
        ev = await safe.events.find_one({"idempotency_key": key})
    verify = (f"certificate {cert_ev['payload'].get('certificate_id')} — scan its QR to verify"
              if cert_ev else "issued with the certificate on finalise — scan the certificate QR")
    sp = signed["payload"]
    if signed["type"] == "ClientAcceptanceRecorded":
        acc = (f"{sp['label']} — {sp['typed_name']}, {_syd(signed['ts'])}, doc SHA-256 {sp['doc_sha256']}, "
               f"ledger event {signed['event_id']}")
    elif signed["type"] == "OfflineSignatureRecorded":
        acc = f"{sp['label']} — scan SHA-256 {sp['scan_sha256']}, reason: {sp['reason']}, ledger event {signed['event_id']}"
    else:
        acc = f"MOCKED e-sign, ledger event {signed['event_id']}"
    text = body + f"Acceptance : {acc}\nLedger hash: {ev['event_hash']}\nVerify     : {verify}\n"
    return PlainTextResponse(text, media_type="text/plain; charset=utf-8")
