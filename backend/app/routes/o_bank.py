"""R1.5 — /api/o/bank (Operator + Principal), /api/o/payment-settings, /api/c/money."""
from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from app.db.tenant_safe import wrap
from app.kernel import bankfeed as bf
from app.kernel import object_store, payments
from app.kernel.tenant_ctx import use_tenant
from app.middleware.client_guard import member_clients
from app.middleware.session import require_operator_session, require_session

router = APIRouter(prefix="/api/o", tags=["bank"])
c_router = APIRouter(prefix="/api/c", tags=["money"])
LABEL = ("This trust record supports compliance. It does not replace the brokerage's accounting system "
         "or its trust account audit.")


def _store(request: Request):
    return request.app.state.mongo_db


async def staff(s=Depends(require_operator_session)):
    if s.active_role not in ("operator", "principal"):
        raise HTTPException(status_code=403, detail="Only the Operator or a Principal can open the bank feed.")
    return s


def _bad(e: Exception):
    raise HTTPException(status_code=422, detail=str(e)) from None


def _clean(rows):
    for r in rows:
        r.pop("_id", None)
    return rows


@router.get("/payment-settings")
async def get_settings(request: Request, s=Depends(staff)) -> dict:
    return {"settings": await payments.get_settings(_store(request), s.tenant_id), "rules": payments.RULES}


class SettingsBody(BaseModel):
    account_name: str
    bsb: str
    account_number: str
    biller_code: str | None = None
    reference_rule: str = "crn"


@router.put("/payment-settings")
async def put_settings(body: SettingsBody, request: Request, s=Depends(staff)) -> dict:
    if s.active_role != "operator":
        raise HTTPException(status_code=403, detail="Only the Operator can change payment details.")
    try:
        return {"settings": await payments.save_settings(_store(request), s.tenant_id, body.model_dump(), s.user_id, s.active_role)}
    except payments.Invalid as e:
        _bad(e)


@router.post("/bank/statements")
async def upload(request: Request, file: UploadFile = File(...), mapping: str | None = Form(None), s=Depends(staff)) -> dict:
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="That statement is over 10 MB.")
    store = _store(request)
    ext = (file.filename or "x.csv").rsplit(".", 1)[-1].lower()
    path = f"{object_store.APP_NAME}/bank/{s.tenant_id}/{uuid.uuid4()}.{ext}"
    try:
        bf.parse(data, file.filename or "", json.loads(mapping) if mapping else None)
    except bf.NeedsMapping as e:
        return {"needs_mapping": True, "headers": e.headers, "fields": list(bf.GENERIC)}
    except (ValueError, IndexError):
        raise HTTPException(status_code=422, detail="We couldn't read that statement. Export it as CSV or OFX and try again.") from None
    await object_store.put_object(path, data, "text/csv" if ext == "csv" else "application/x-ofx")
    return await bf.import_statement(store, s.tenant_id, data=data, filename=file.filename or "", user_id=s.user_id, storage_path=path,
                                     mapping=json.loads(mapping) if mapping else None)


@router.get("/bank/statements")
async def statements(request: Request, s=Depends(staff)) -> dict:
    async with use_tenant(s.tenant_id):
        return {"statements": _clean([x async for x in wrap(_store(request)).bank_statements.find({"test": {"$ne": True}})
                                      .sort("period_from", -1).limit(100)])}


@router.get("/bank/txns")
async def txns(request: Request, status: str | None = None, s=Depends(staff)) -> dict:
    q = {"test": {"$ne": True}, **({"status": {"$in": status.split(",")}} if status else {})}
    async with use_tenant(s.tenant_id):
        return {"txns": _clean([x async for x in wrap(_store(request)).bank_txns.find(q).sort("date", -1).limit(500)])}


class MatchBody(BaseModel):
    payable_id: str


class ReasonBody(BaseModel):
    reason: str


class PaymentBody(BaseModel):
    kind: str
    insurer: str | None = None
    client_id: str | None = None
    policy_id: str | None = None
    note: str = ""
    entry_id: str | None = None
    amount: str | None = None
    date: str | None = None


async def _do(fn):
    try:
        return await fn
    except LookupError:
        raise HTTPException(status_code=404, detail="not_found") from None
    except ValueError as e:
        _bad(e)


@router.post("/bank/txns/{txn_id}/match")
async def match(txn_id: str, body: MatchBody, request: Request, s=Depends(staff)) -> dict:
    return {"status": await _do(bf.confirm_match(_store(request), s.tenant_id, txn_id, body.payable_id, s.user_id, s.active_role))}


@router.post("/bank/txns/{txn_id}/review")
async def review(txn_id: str, request: Request, s=Depends(staff)) -> dict:
    await bf.review_auto(_store(request), s.tenant_id, txn_id, s.user_id)
    return {"ok": True}


@router.post("/bank/txns/{txn_id}/unmatch")
async def unmatch(txn_id: str, body: ReasonBody, request: Request, s=Depends(staff)) -> dict:
    await _do(bf.unmatch(_store(request), s.tenant_id, txn_id, body.reason, s.user_id, s.active_role))
    return {"ok": True}


@router.post("/bank/txns/{txn_id}/payment")
async def txn_payment(txn_id: str, body: PaymentBody, request: Request, s=Depends(staff)) -> dict:
    return await _do(bf.record_payment(_store(request), s.tenant_id, by=s.user_id, role=s.active_role, kind=body.kind, txn_id=txn_id,
                                       insurer=body.insurer, client_id=body.client_id, policy_id=body.policy_id, note=body.note,
                                       entry_id=body.entry_id))


@router.post("/bank/trust/payments")
async def manual_payment(body: PaymentBody, request: Request, s=Depends(staff)) -> dict:
    if not body.amount:
        raise HTTPException(status_code=422, detail="Enter the amount paid.")
    return await _do(bf.record_payment(_store(request), s.tenant_id, by=s.user_id, role=s.active_role, kind=body.kind, amount=body.amount,
                                       insurer=body.insurer, client_id=body.client_id, policy_id=body.policy_id, note=body.note,
                                       on_date=body.date))


@router.get("/bank/trust")
async def trust(request: Request, s=Depends(staff)) -> dict:
    r = await bf.reconciliation(_store(request), s.tenant_id, date.today())
    async with use_tenant(s.tenant_id):
        entries = _clean([e async for e in wrap(_store(request)).trust_entries.find({"test": {"$ne": True}}).sort("date", -1).limit(500)])
    return {"label": LABEL, "total": r["trust_balance"], "per_client": r["per_client"], "entries": entries}


@router.get("/bank/payables")
async def payables(request: Request, state: str | None = None, s=Depends(staff)) -> dict:
    q = {"test": {"$ne": True}, **({"state": {"$in": state.split(",")}} if state else {})}
    safe = wrap(_store(request))
    async with use_tenant(s.tenant_id):
        names = {c["client_id"]: c["display_name"] async for c in safe.clients.find({}, {"client_id": 1, "display_name": 1})}
        rows = _clean([p async for p in safe.payables.find(q).sort("created_at", -1).limit(500)])
    return {"payables": [{**p, "client": names.get(p["client_id"])} for p in rows]}


def _as_at(v: str | None) -> date:
    try:
        return date.fromisoformat(v) if v else date.today()
    except ValueError:
        raise HTTPException(status_code=422, detail="as_at must be YYYY-MM-DD") from None


@router.get("/bank/reconciliation")
async def recon(request: Request, as_at: str | None = None, s=Depends(staff)) -> dict:
    r = await bf.reconciliation(_store(request), s.tenant_id, _as_at(as_at))
    async with use_tenant(s.tenant_id):
        offs = _clean([o async for o in wrap(_store(request)).recon_signoffs.find({}).sort("at", -1).limit(20)])
    return {**r, "label": LABEL, "signoffs": offs}


class SignBody(BaseModel):
    as_at: str
    note: str = ""


@router.post("/bank/reconciliation/sign-off")
async def sign(body: SignBody, request: Request, s=Depends(staff)) -> dict:
    if s.active_role != "principal":
        raise HTTPException(status_code=403, detail="Only a Principal can sign off the trust reconciliation.")
    return await _do(bf.sign_off(_store(request), s.tenant_id, _as_at(body.as_at), body.note, s.user_id))


async def _export_rows(request, s, as_at):
    r = await bf.reconciliation(_store(request), s.tenant_id, as_at)
    async with use_tenant(s.tenant_id):
        entries = [e async for e in wrap(_store(request)).trust_entries.find({"test": {"$ne": True}, "date": {"$lte": str(as_at)}})
                   .sort("date", 1)]
    return r, entries


@router.get("/bank/reconciliation/export.csv")
async def export_csv(request: Request, as_at: str | None = None, s=Depends(staff)) -> Response:
    r, entries = await _export_rows(request, s, _as_at(as_at))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([LABEL])
    w.writerow(["Trust reconciliation as at", r["as_at"]])
    for k in ("bank_balance", "trust_balance", "expected_bank_balance", "unexplained"):
        w.writerow([k.replace("_", " "), r[k]])
    for i in r["items"]:
        w.writerow(["explained", i["label"], i["amount"]])
    w.writerow([])
    w.writerow(["date", "kind", "amount", "client_id", "policy_id", "insurer", "on bank statement", "note"])
    for e in entries:
        w.writerow([e["date"], e["kind"], e["amount"], e.get("client_id") or "", e.get("policy_id") or "", e.get("insurer") or "",
                    "yes" if e.get("presented") else "no", e.get("note") or ""])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="trust-reconciliation-{r["as_at"]}.csv"'})


@router.get("/bank/reconciliation/export.pdf")
async def export_pdf(request: Request, as_at: str | None = None, s=Depends(staff)) -> Response:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table
    r, entries = await _export_rows(request, s, _as_at(as_at))
    st = getSampleStyleSheet()
    buf = io.BytesIO()
    summary = [["Bank balance", r["bank_balance"]], ["Trust record balance", r["trust_balance"]],
               *[[i["label"], i["amount"]] for i in r["items"]], ["Unexplained difference", r["unexplained"]]]
    rows = [["Date", "Kind", "Amount", "Client", "On statement", "Note"]] + [
        [e["date"], e["kind"], e["amount"], (e.get("client_id") or "")[:8], "yes" if e.get("presented") else "no", (e.get("note") or "")[:40]]
        for e in entries]
    SimpleDocTemplate(buf, pagesize=A4).build([Paragraph(f"Trust reconciliation as at {r['as_at']}", st["Title"]),
                                              Paragraph(LABEL, st["Italic"]), Spacer(1, 12), Table(summary), Spacer(1, 12), Table(rows)])
    return Response(buf.getvalue(), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="trust-reconciliation-{r["as_at"]}.pdf"'})


@c_router.get("/money")
async def client_money(request: Request, s=Depends(require_session)) -> dict:
    safe = wrap(_store(request))
    async with use_tenant(s.tenant_id):
        cids = await member_clients(safe, s.user_id)
        rows = _clean([p async for p in safe.payables.find({"client_id": {"$in": list(cids)}}).sort("created_at", -1).limit(100)])
        pols = {p["policy_id"]: p.get("product") async for p in safe.policies.find({"policy_id": {"$in": [r["policy_id"] for r in rows]}})}
    return {"items": [{k: r[k] for k in ("payable_id", "crn", "amount", "currency", "state", "due_date", "policy_id", "envelope_id",
                                         "received")} | {"product": pols.get(r["policy_id"])} for r in rows]}
