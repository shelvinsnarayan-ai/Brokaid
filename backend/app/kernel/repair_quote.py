"""R3.8 — repair quote summaries. Staff type, or accept deterministic suggestions from the PDF text (pypdf + regex; never an LLM).
The client sees repairer, total incl GST and quote date only, from the latest non-test quote. Nothing about cover, excess or approval."""
from __future__ import annotations

import html
import io
import re
from datetime import date
from decimal import Decimal, InvalidOperation

FIELDS = ("repairer", "total", "quote_no", "quote_date")
_AMT = r"A?\$\s?([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})|[0-9]+(?:\.[0-9]{2}))"
_TOTAL = re.compile(r"total[^\n]{0,30}?(?:inc(?:l(?:uding)?)?\.?\s*gst)[^\n]*?(?:\n\s*)?" + _AMT, re.IGNORECASE)
_TOTAL_ANY = re.compile(r"\btotal\b(?![^\n]*ex\s*gst)[^\n]{0,30}?(?:\n\s*)?" + _AMT, re.IGNORECASE)
_QNO = re.compile(r"\bquote\s*(?:no\.?|number|#)?\s*[:\-]?\s*([A-Z]{1,4}-?\d{2,8})\b", re.IGNORECASE)
_DATE = re.compile(r"\b(?:quote\s*)?date\s*[:\-]?\s*(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})\b", re.IGNORECASE)
_NOT_NAME = re.compile(r"\b(sample|quote|abn|invoice|tax|page|date|vehicle|rego)\b", re.IGNORECASE)


def _money(s: str) -> str | None:
    try:
        v = Decimal(s.replace(",", "")).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    return f"{v:.2f}" if Decimal(1) <= v <= Decimal(1000000) else None


def suggest(pdf: bytes) -> dict:
    """Best-effort suggestions; any field it can't find is left out."""
    try:
        import pypdf
        text = "\n".join((p.extract_text() or "") for p in pypdf.PdfReader(io.BytesIO(pdf)).pages[:3])
    except Exception:  # noqa: BLE001
        return {}
    out: dict = {}
    m = _TOTAL.search(text) or _TOTAL_ANY.search(text)
    if not m or not (t := _money(m.group(1))):
        return {}  # no total → no suggestion at all
    out["total"] = t
    if m2 := _QNO.search(text):
        out["quote_no"] = m2.group(1).upper()
    if m3 := _DATE.search(text):
        try:
            out["quote_date"] = date(int(m3[3]), int(m3[2]), int(m3[1])).isoformat()
        except ValueError:
            pass
    for line in text.splitlines()[:6]:
        line = line.strip()
        if 3 <= len(line) <= 120 and re.search(r"[A-Za-z]{3}", line) and not _NOT_NAME.search(line) and not re.search(r"\d{3,}", line):
            out["repairer"] = line
            break
    return out


def clean(raw: dict | None, suggested: dict | None = None) -> dict | None:
    """Validated summary, or None when repairer/total are blank (no card). Records whether each value was suggested or typed."""
    raw, suggested = raw or {}, suggested or {}
    repairer = " ".join(str(raw.get("repairer") or "").split())
    total = str(raw.get("total") or "").replace("A$", "").replace("$", "").replace(",", "").strip()
    if not repairer and not total:
        return None
    if not repairer or len(repairer) > 120:
        raise ValueError("Repairer name is required (up to 120 characters).")
    if not re.fullmatch(r"\d{1,7}(\.\d{1,2})?", total) or not (t := _money(total)):
        raise ValueError("Total must be between A$1 and A$1,000,000, e.g. 2340.00.")
    qno = " ".join(str(raw.get("quote_no") or "").split())[:40] or None
    qdate = str(raw.get("quote_date") or "").strip() or None
    if qdate:
        try:
            qdate = date.fromisoformat(qdate).isoformat()
        except ValueError as e:
            raise ValueError("Quote date must be a date (YYYY-MM-DD).") from e
    vals = {"repairer": html.escape(repairer, quote=False), "total": t, "quote_no": qno and html.escape(qno, quote=False), "quote_date": qdate}
    src = {k: ("suggested" if suggested.get(k) and str(suggested.get(k)) == str(raw.get(k) or "").strip() else "typed")
           for k in FIELDS if vals[k]}
    return {**vals, "source": src}


def summary(c: dict, hidden_replies: set[str]) -> dict | None:
    """Client card data: latest quote first, plus the list. Test-flagged files and replies are left out."""
    rows = [d for d in c.get("docs") or [] if d.get("label") == "repair_quote" and d.get("quote") and d.get("client_visible")
            and not d.get("test") and d.get("reply_id") not in hidden_replies]
    if not rows:
        return None
    rows.sort(key=lambda d: d["uploaded_at"], reverse=True)
    items = [{"file_id": d["file_id"], "repairer": d["quote"]["repairer"], "total": d["quote"]["total"], "quote_no": d["quote"].get("quote_no"),
              "quote_date": d["quote"].get("quote_date"), "received_at": d["uploaded_at"].isoformat() if hasattr(d["uploaded_at"], "isoformat")
              else d["uploaded_at"], "filename": d["filename"]} for d in rows]
    return {"latest": items[0], "count": len(items), "items": items}
