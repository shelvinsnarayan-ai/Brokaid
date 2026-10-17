"""R1.5 — bank statement import, deterministic matching, trust record and reconciliation.

Formats: CBA / ANZ (headerless CSV), NAB / Westpac (headered CSV), OFX/QFX, plus a mapping fallback.
PremiumReceived is emitted ONLY here (record_receipt), from a bank statement line.
"""
from __future__ import annotations

import csv
import difflib
import hashlib
import io
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from pymongo.errors import DuplicateKeyError

from app.db.tenant_safe import wrap
from app.kernel import payments
from app.kernel.payments import Q, dec
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch

GENERIC = {"date": ("date", "transaction date", "posted", "posting date", "value date"), "amount": ("amount", "transaction amount"),
           "debit": ("debit", "debit amount", "withdrawal", "withdrawals"), "credit": ("credit", "credit amount", "deposit", "deposits"),
           "description": ("description", "narrative", "transaction details", "details", "particulars", "memo"),
           "balance": ("balance", "running balance"), "reference": ("reference", "ref", "crn")}
OPEN = ("owed", "advised", "partial")


class NeedsMapping(ValueError):
    def __init__(self, headers):
        super().__init__("We couldn't tell which columns are which. Match them below.")
        self.headers = headers


def _n(s) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(s or "").lower())).strip()


def _date(s: str) -> date:
    s = s.strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%d %b %y", "%d %b %Y", "%d-%b-%y", "%d-%b-%Y", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"bad date {s!r}")


def _amt(s) -> Decimal | None:
    s = str(s or "").replace("$", "").replace(",", "").replace(" ", "")
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")
    try:
        v = Decimal(s.strip("()").replace("CR", "").replace("DR", ""))
    except InvalidOperation:
        raise ValueError(f"bad amount {s!r}") from None
    return (-v if neg or s.endswith("DR") else v).quantize(Q)


def _is_date(s) -> bool:
    try:
        _date(str(s))
        return True
    except ValueError:
        return False


def _ofx(text: str) -> tuple[str, list[dict], Decimal | None]:
    txns = []
    for k, blk in enumerate(re.split(r"<STMTTRN>", text, flags=re.I)[1:]):
        def tag(t):
            m = re.search(rf"<{t}>([^<\r\n]+)", blk, flags=re.I)
            return m.group(1).strip() if m else ""
        txns.append({"date": _date(tag("DTPOSTED")[:8]), "amount": _amt(tag("TRNAMT")),
                     "description": " ".join(x for x in (tag("NAME"), tag("MEMO")) if x), "balance": None, "ref": tag("FITID") or None})
    m = re.search(r"<LEDGERBAL>.*?<BALAMT>([^<\r\n]+)", text, flags=re.I | re.S)
    return "ofx", txns, (_amt(m.group(1)) if m else None)


def parse(data: bytes, filename: str, mapping: dict | None = None) -> dict:
    text = data.decode("utf-8-sig", errors="replace")
    closing = None
    if "<OFX>" in text.upper() or "OFXHEADER" in text.upper():
        fmt, txns, closing = _ofx(text)
    else:
        rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
        if not rows:
            raise ValueError("empty statement")
        head = [h.strip() for h in rows[0]]
        if _is_date(head[0]) and mapping is None:
            if len(head) >= 4 and _amt(rows[0][3]) is not None:
                fmt, txns = "cba", [{"date": _date(r[0]), "amount": _amt(r[1]), "description": r[2].strip(), "balance": _amt(r[3]),
                                     "ref": None} for r in rows]
            else:
                fmt, txns = "anz", [{"date": _date(r[0]), "amount": _amt(r[1]), "description": r[2].strip(), "balance": None, "ref": None}
                                    for r in rows]
        else:
            idx = {_n(h): i for i, h in enumerate(head)}
            if mapping:
                col = {f: head.index(h) for f, h in mapping.items() if h in head}
                fmt = "mapped"
            elif "transaction details" in idx and "balance" in idx and "amount" in idx:
                col, fmt = {"date": idx["date"], "amount": idx["amount"], "description": idx["transaction details"], "balance": idx["balance"]}, "nab"
            elif "narrative" in idx and "debit amount" in idx:
                col, fmt = {"date": idx["date"], "debit": idx["debit amount"], "credit": idx["credit amount"],
                            "description": idx["narrative"], "balance": idx["balance"]}, "westpac"
            else:
                col = {f: next((idx[s] for s in syns if s in idx), None) for f, syns in GENERIC.items()}
                col = {k: v for k, v in col.items() if v is not None}
                fmt = "generic"
            if "date" not in col or "description" not in col or not ({"amount"} <= col.keys() or {"debit", "credit"} & col.keys()):
                raise NeedsMapping(head)
            txns = []
            for r in rows[1:]:
                g = lambda f: (r[col[f]] if f in col and col[f] < len(r) else "")  # noqa: E731
                amt = _amt(g("amount")) if "amount" in col else (_amt(g("credit")) or Decimal("0")) - (_amt(g("debit")) or Decimal("0"))
                txns.append({"date": _date(g("date")), "amount": amt.quantize(Q), "description": g("description").strip(),
                             "balance": _amt(g("balance")), "ref": g("reference").strip() or None})
    for i, t in enumerate(txns):
        t["seq"] = i
    return {"format": fmt, "txns": txns, "closing_hint": closing}


def _chrono(txns: list[dict]) -> list[dict]:
    """Bank exports are newest-first or oldest-first; pick the order whose balances chain."""
    if len(txns) < 2 or any(t["balance"] is None for t in txns):
        return sorted(txns, key=lambda t: (t["date"], t["seq"]))

    def fit(ts):
        return sum(ts[i - 1]["balance"] + ts[i]["amount"] == ts[i]["balance"] for i in range(1, len(ts)))
    fwd, rev = txns, list(reversed(txns))
    return fwd if fit(fwd) >= fit(rev) else rev


def continuity(txns: list[dict], closing_hint=None) -> dict:
    ts = _chrono(txns)
    if not ts:
        return {"ok": True, "gaps": [], "opening": None, "closing": None, "checked": False}
    if ts[0]["balance"] is None:
        closing = closing_hint
        opening = (closing - sum(t["amount"] for t in ts)).quantize(Q) if closing is not None else None
        return {"ok": True, "gaps": [], "opening": opening, "closing": closing, "checked": False}
    gaps = [{"after": str(ts[i - 1]["date"]), "before": str(ts[i]["date"]),
             "missing_amount": str((ts[i]["balance"] - ts[i - 1]["balance"] - ts[i]["amount"]).quantize(Q))}
            for i in range(1, len(ts)) if ts[i - 1]["balance"] + ts[i]["amount"] != ts[i]["balance"]]
    return {"ok": not gaps, "gaps": gaps, "opening": (ts[0]["balance"] - ts[0]["amount"]).quantize(Q), "closing": ts[-1]["balance"],
            "checked": True}


def fingerprints(txns: list[dict]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for t in txns:
        base = f"{t['date']}|{t['amount']}|{_n(t['description'])}|{t['balance'] if t['balance'] is not None else ''}|{t['ref'] or ''}"
        k = seen.get(base, 0)
        seen[base] = k + 1
        out.append(hashlib.sha256(f"{base}#{k}".encode()).hexdigest())
    return out


async def ensure_indexes(store) -> None:
    safe = wrap(store)
    await safe.bank_txns.create_index([("tenant_id", 1), ("fingerprint", 1)], unique=True, name="bank_txn_fp_uniq")
    await safe.bank_statements.create_index([("tenant_id", 1), ("sha256", 1)], unique=True, name="bank_stmt_sha_uniq")
    await safe.payables.create_index([("tenant_id", 1), ("crn", 1)], unique=True, name="payable_crn_uniq")


def _s(v) -> str | None:
    return None if v is None else str(v)


async def _event(store, tid, typ, payload, idem, by="system", role="bank-feed"):
    await append_via_dispatch(store, tid, typ, actor={"kind": "human" if by != "system" else "system", "id": by, "name": role},
                              payload=payload, idempotency_key=idem)


async def import_statement(store, tid: str, *, data: bytes, filename: str, user_id: str, storage_path: str | None,
                           mapping: dict | None = None, test: bool = False) -> dict:
    sha = hashlib.sha256(data).hexdigest()
    safe = wrap(store)
    async with use_tenant(tid):
        dup = await safe.bank_statements.find_one({"sha256": sha}, {"_id": 0})
    if dup:
        return {"duplicate_file": True, "statement": dup}
    p = parse(data, filename, mapping)
    cont = continuity(p["txns"], p["closing_hint"])
    fps = fingerprints(p["txns"])
    sid = str(uuid.uuid4())
    dates = [t["date"] for t in p["txns"]]
    new, dups = [], 0
    async with use_tenant(tid):
        prev = [s async for s in safe.bank_statements.find({"test": {"$ne": True}, "period_to": {"$lt": str(min(dates))}})
                .sort("period_to", -1).limit(1)] if dates and not test else []
        for t, fp in zip(p["txns"], fps):
            doc = {"txn_id": str(uuid.uuid4()), "fingerprint": fp, "statement_id": sid, "statement_sha": sha, "date": str(t["date"]),
                   "amount": str(t["amount"]), "description": t["description"], "balance": _s(t["balance"]), "ref": t["ref"],
                   "direction": "in" if t["amount"] > 0 else "out", "status": "new", "match": None, "suggestions": [],
                   "reversals": 0, "history": [], "test": test, "imported_at": datetime.now(timezone.utc)}
            if await safe.bank_txns.find_one({"fingerprint": fp}, {"_id": 1}):
                dups += 1
                continue
            try:  # the unique index catches a concurrent import of the same line
                await safe.bank_txns.insert_one(dict(doc))
                new.append(doc)
            except DuplicateKeyError:
                dups += 1
    gap_prev = None
    if prev and prev[0].get("closing") is not None and cont["opening"] is not None and dec(prev[0]["closing"]) != cont["opening"]:
        gap_prev = {"after": prev[0]["period_to"], "before": str(min(dates)),
                    "missing_amount": str((cont["opening"] - dec(prev[0]["closing"])).quantize(Q))}
    st = {"statement_id": sid, "sha256": sha, "filename": filename, "format": p["format"], "storage_path": storage_path,
          "period_from": str(min(dates)) if dates else None, "period_to": str(max(dates)) if dates else None,
          "opening": _s(cont["opening"]), "closing": _s(cont["closing"]), "continuity": {**cont, "opening": _s(cont["opening"]),
          "closing": _s(cont["closing"]), "gap_from_previous": gap_prev}, "txn_count": len(p["txns"]), "new_txns": len(new),
          "duplicate_txns": dups, "uploaded_by": user_id, "at": datetime.now(timezone.utc), "test": test}
    async with use_tenant(tid):
        await safe.bank_statements.insert_one(dict(st))
    await _event(store, tid, "BankStatementImported", {"statement_id": sid, "sha256": sha, "format": p["format"], "txns": len(p["txns"]),
                                                       "new": len(new), "duplicates": dups, "continuity_ok": cont["ok"] and not gap_prev,
                                                       "test": test}, f"bankstmt:{sha}", user_id, "statement import")
    for t in new:
        await match_txn(store, tid, t)
    st.pop("_id", None)
    return {"duplicate_file": False, "statement": st}


def _crns(txn: dict) -> list[str]:
    text = f"{txn.get('description') or ''} {txn.get('ref') or ''}"
    return [c for c in re.findall(r"(?<!\d)(\d{6,20})(?!\d)", text) if payments.crn_valid(c)]


async def match_txn(store, tid: str, txn: dict, *, auto: bool = True) -> str:
    """auto=False (after an unmatch): suggestions only — a person decides, the CRN doesn't re-match by itself."""
    safe = wrap(store)
    amt = dec(txn["amount"])
    async with use_tenant(tid):
        if amt < 0:
            await safe.bank_txns.update_one({"txn_id": txn["txn_id"]}, {"$set": {"status": "outgoing"}})
            return "outgoing"
        for crn in (_crns(txn) if auto else []):  # test statements only ever match test items, and real ones only real items
            p = await safe.payables.find_one({"crn": crn, "test": True if txn.get("test") else {"$ne": True}})
            if p:
                break
        else:
            p = None
    if p:
        return await record_receipt(store, tid, txn, p, by="system", kind="auto_crn")
    async with use_tenant(tid):
        names = {c["client_id"]: c["display_name"] async for c in safe.clients.find({}, {"client_id": 1, "display_name": 1})}
        sugg = []
        async for p in safe.payables.find({"state": {"$in": list(OPEN)}, "test": True if txn.get("test") else {"$ne": True}}):
            out = dec(p["amount"]) - dec(p["received"])
            name = _n(names.get(p["client_id"], ""))
            desc = _n(txn["description"])
            sim = max((difflib.SequenceMatcher(None, name, desc).ratio(),
                       sum(w in desc.split() for w in name.split() if len(w) > 2) / max(1, len(name.split()))), default=0)
            score = (0.6 if out == amt else 0) + 0.4 * sim
            if out == amt or sim >= 0.8:
                sugg.append({"payable_id": p["payable_id"], "crn": p["crn"], "client": names.get(p["client_id"]), "amount": str(out),
                             "score": round(score, 2), "why": " + ".join(x for x in ("same amount" if out == amt else "",
                                                                                      "name looks similar" if sim >= 0.5 else "") if x)})
        sugg = sorted(sugg, key=lambda s: -s["score"])[:3]
        status = "suggested" if sugg else "unmatched"
        await safe.bank_txns.update_one({"txn_id": txn["txn_id"]}, {"$set": {"status": status, "suggestions": sugg}})
    return status


TXN_STATUS = {"received": "matched", "partial": "partial", "overpaid": "overpaid"}


async def record_receipt(store, tid: str, txn: dict, payable: dict, *, by: str, kind: str, role: str = "bank-feed") -> str:
    """The only place PremiumReceived is produced: a bank statement line linked to a payable."""
    safe = wrap(store)
    amt = dec(txn["amount"])
    async with use_tenant(tid):
        p = await safe.payables.find_one({"payable_id": payable["payable_id"]})
        p["received"] = str(dec(p["received"]) + amt)
        p["state"] = payments.state_of(p)
        await safe.payables.update_one({"payable_id": p["payable_id"]}, {"$set": {"received": p["received"], "state": p["state"]}})
        status = TXN_STATUS[p["state"]]
        match = {"payable_id": p["payable_id"], "crn": p["crn"], "kind": kind, "by": by, "at": datetime.now(timezone.utc),
                 "review_required": kind == "auto_crn", "reviewed": kind == "human"}
        await safe.bank_txns.update_one({"txn_id": txn["txn_id"]}, {"$set": {"status": status, "match": match, "suggestions": []}})
        await safe.trust_entries.insert_one({"entry_id": str(uuid.uuid4()), "date": txn["date"], "kind": "receipt", "amount": str(amt),
                                             "client_id": p["client_id"], "policy_id": p["policy_id"], "payable_id": p["payable_id"],
                                             "txn_id": txn["txn_id"], "presented": True, "note": f"CRN {p['crn']}",
                                             "test": bool(txn.get("test")), "at": datetime.now(timezone.utc)})
    await _event(store, tid, "PremiumReceived", {
        "source": "bank_statement", "statement_hash": txn["statement_sha"], "txn_fingerprint": txn["fingerprint"],
        "amount": str(amt), "crn": p["crn"], "payable_id": p["payable_id"], "envelope_id": p["envelope_id"],
        "policy_id": p["policy_id"], "client_id": p["client_id"], "match": kind, "state": p["state"], "test": bool(txn.get("test"))},
        f"premium_received:{txn['fingerprint']}:{txn.get('reversals', 0)}", by, role)
    return status


async def _txn(safe, txn_id):
    t = await safe.bank_txns.find_one({"txn_id": txn_id})
    if not t:
        raise LookupError("not_found")
    return t


async def confirm_match(store, tid, txn_id, payable_id, by, role) -> str:
    safe = wrap(store)
    async with use_tenant(tid):
        t = await _txn(safe, txn_id)
        p = await safe.payables.find_one({"payable_id": payable_id})
    if not p or t["direction"] != "in" or t["status"] not in ("unmatched", "suggested"):
        raise ValueError("This line can't be matched to that item.")
    return await record_receipt(store, tid, t, p, by=by, kind="human", role=role)


async def review_auto(store, tid, txn_id, by) -> None:
    async with use_tenant(tid):
        await wrap(store).bank_txns.update_one({"txn_id": txn_id, "match.kind": "auto_crn"},
                                               {"$set": {"match.reviewed": True, "match.reviewed_by": by}})


async def unmatch(store, tid, txn_id, reason, by, role) -> None:
    if not (reason or "").strip():
        raise ValueError("Give a reason for unmatching.")
    safe = wrap(store)
    async with use_tenant(tid):
        t = await _txn(safe, txn_id)
        if not t.get("match"):
            raise ValueError("This line isn't matched.")
        amt = dec(t["amount"])
        p = await safe.payables.find_one({"payable_id": t["match"]["payable_id"]})
        p["received"] = str(dec(p["received"]) - amt)
        p["state"] = payments.state_of(p)
        await safe.payables.update_one({"payable_id": p["payable_id"]}, {"$set": {"received": p["received"], "state": p["state"]}})
        await safe.bank_txns.update_one({"txn_id": txn_id}, {"$set": {"status": "unmatched", "match": None},
                                                             "$inc": {"reversals": 1},
                                                             "$push": {"history": {**t["match"], "unmatched_by": by, "reason": reason}}})
        await safe.trust_entries.insert_one({"entry_id": str(uuid.uuid4()), "date": t["date"], "kind": "reversal", "amount": str(-amt),
                                             "client_id": p["client_id"], "policy_id": p["policy_id"], "payable_id": p["payable_id"],
                                             "txn_id": txn_id, "presented": True, "note": reason, "test": bool(t.get("test")),
                                             "at": datetime.now(timezone.utc)})
    await _event(store, tid, "PremiumReceiptReversed", {"txn_fingerprint": t["fingerprint"], "payable_id": p["payable_id"],
                                                        "amount": str(amt), "reason": reason, "client_id": p["client_id"]},
                 f"premium_reversed:{t['fingerprint']}:{t.get('reversals', 0)}", by, role)
    await match_txn(store, tid, {**t, "status": "unmatched", "match": None}, auto=False)


async def record_payment(store, tid, *, by, role, kind, amount=None, txn_id=None, insurer=None, client_id=None,
                         policy_id=None, note="", on_date=None, entry_id=None) -> dict:
    """Money out of trust: to an insurer or a refund. From an outgoing bank line, or manual (unpresented)."""
    if kind not in ("insurer_payment", "refund"):
        raise ValueError("Choose insurer payment or refund.")
    if kind == "insurer_payment" and not insurer:
        raise ValueError("Choose the insurer paid.")
    safe = wrap(store)
    async with use_tenant(tid):
        t = await _txn(safe, txn_id) if txn_id else None
        if t and (t["direction"] != "out" or t["status"] != "outgoing"):
            raise ValueError("Only an unallocated outgoing line can be recorded as a payment.")
        if entry_id and t:  # a manual entry now shows on the bank statement
            await safe.trust_entries.update_one({"entry_id": entry_id, "presented": False}, {"$set": {"presented": True, "txn_id": txn_id}})
            await safe.bank_txns.update_one({"txn_id": txn_id}, {"$set": {"status": "allocated", "allocation": {"entry_id": entry_id}}})
            return {"entry_id": entry_id}
        amt = dec(t["amount"]) if t else -abs(dec(amount))
        e = {"entry_id": str(uuid.uuid4()), "date": t["date"] if t else (on_date or str(date.today())), "kind": kind, "amount": str(amt),
             "client_id": client_id, "policy_id": policy_id, "insurer": insurer, "txn_id": txn_id, "presented": bool(t), "note": note,
             "test": bool(t and t.get("test")), "at": datetime.now(timezone.utc)}
        await safe.trust_entries.insert_one(dict(e))
        if t:
            await safe.bank_txns.update_one({"txn_id": txn_id}, {"$set": {"status": "allocated", "allocation": {"entry_id": e["entry_id"],
                                                                                                                  "kind": kind}}})
    await _event(store, tid, "TrustPaymentRecorded", {"entry_id": e["entry_id"], "kind": kind, "amount": e["amount"], "insurer": insurer,
                                                      "client_id": client_id, "source": "bank_statement" if t else "manual",
                                                      "txn_fingerprint": t and t["fingerprint"]}, f"trustpay:{e['entry_id']}", by, role)
    return {"entry_id": e["entry_id"]}


async def reconciliation(store, tid: str, as_at: date) -> dict:
    safe = wrap(store)
    d = str(as_at)
    async with use_tenant(tid):
        stmts = [s async for s in safe.bank_statements.find({"test": {"$ne": True}, "period_from": {"$lte": d}}).sort("period_from", 1)]
        txns = [t async for t in safe.bank_txns.find({"test": {"$ne": True}, "date": {"$lte": d}})]
        entries = [e async for e in safe.trust_entries.find({"test": {"$ne": True}, "date": {"$lte": d}})]
        names = {c["client_id"]: c["display_name"] async for c in safe.clients.find({}, {"client_id": 1, "display_name": 1})}
    opening = dec(stmts[0]["opening"]) if stmts and stmts[0].get("opening") is not None else Decimal("0.00")
    computed = (opening + sum((dec(t["amount"]) for t in txns), Decimal("0"))).quantize(Q)
    with_bal = sorted((t for t in txns if t.get("balance") is not None), key=lambda t: (t["date"], t["imported_at"]))
    stated = dec(with_bal[-1]["balance"]) if with_bal else computed
    trust = sum((dec(e["amount"]) for e in entries), Decimal("0")).quantize(Q)
    gaps = [g for s in stmts for g in (s["continuity"].get("gaps") or []) + ([s["continuity"]["gap_from_previous"]]
                                                                             if s["continuity"].get("gap_from_previous") else [])]
    items = [
        ("opening", "Opening bank balance brought forward (before the first imported statement)", opening),
        ("unmatched_in", "Money received but not yet matched to a client", sum((dec(t["amount"]) for t in txns
                                                                                if t["status"] in ("unmatched", "suggested")), Decimal("0"))),
        ("unallocated_out", "Money paid out of the account but not yet recorded in the trust record",
         sum((dec(t["amount"]) for t in txns if t["status"] == "outgoing"), Decimal("0"))),
        ("unpresented", "Trust payments recorded but not yet on a bank statement",
         -sum((dec(e["amount"]) for e in entries if not e.get("presented")), Decimal("0"))),
        ("gaps", "Missing statement lines (the running balance jumps)", sum((dec(g["missing_amount"]) for g in gaps), Decimal("0"))),
    ]
    expected = (trust + sum(v for _, _, v in items)).quantize(Q)
    per_client: dict[str, Decimal] = {}
    for e in entries:
        if e.get("client_id"):
            per_client[e["client_id"]] = per_client.get(e["client_id"], Decimal("0")) + dec(e["amount"])
    return {"as_at": d, "bank_balance": str(stated), "computed_bank_balance": str(computed), "trust_balance": str(trust),
            "items": [{"key": k, "label": lbl, "amount": str(v.quantize(Q))} for k, lbl, v in items if v],
            "expected_bank_balance": str(expected), "unexplained": str((stated - expected).quantize(Q)),
            "balanced": stated == expected, "gaps": gaps,
            "per_client": sorted([{"client_id": k, "client": names.get(k, k), "balance": str(v.quantize(Q))} for k, v in per_client.items()
                                  if v], key=lambda x: x["client"]),
            "open_items": {"unmatched": sum(t["status"] in ("unmatched", "suggested") for t in txns),
                           "outgoing": sum(t["status"] == "outgoing" for t in txns),
                           "unpresented": sum(not e.get("presented") for e in entries)}}


async def sign_off(store, tid: str, as_at: date, note: str, by: str) -> dict:
    r = await reconciliation(store, tid, as_at)
    if not r["balanced"] and not (note or "").strip():
        raise ValueError("The reconciliation doesn't balance. Add a note explaining the difference to sign off.")
    snap = hashlib.sha256(repr(sorted(r.items())).encode()).hexdigest()
    sid = str(uuid.uuid4())
    async with use_tenant(tid):
        await wrap(store).recon_signoffs.insert_one({"signoff_id": sid, "as_at": r["as_at"], "by": by, "note": note, "snapshot_sha": snap,
                                                    "summary": {k: r[k] for k in ("bank_balance", "trust_balance", "unexplained")},
                                                    "at": datetime.now(timezone.utc)})
    await _event(store, tid, "TrustReconciliationSignedOff", {"signoff_id": sid, "as_at": r["as_at"], "bank_balance": r["bank_balance"],
                                                              "trust_balance": r["trust_balance"], "unexplained": r["unexplained"],
                                                              "balanced": r["balanced"], "note": note, "snapshot_sha": snap},
                 f"reconsign:{sid}", by, "principal")
    return {"signoff_id": sid, **r}


def business_days_between(a: date, b: date) -> int:
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        n += d.weekday() < 5
    return n
