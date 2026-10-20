"""R2.2 — one-page, tenant-branded renewal comparison PDF for the client (safe to forward: no CRN, bank details or ids).

Deterministic per content version: the same content gives byte-identical PDFs (reportlab invariant mode, stored once).
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from xml.sax.saxutils import escape

from app.db.tenant_safe import wrap
from app.kernel import holidays, object_store
from app.kernel import renewals as rn
from app.kernel.holidays import SYD
from app.kernel.tenant_ctx import use_tenant

DEMO_LABEL = "Example price (demo)"
RENDER_VERSION = 2  # bump when the layout changes so the content key (and stored PDF) changes too
PDS_LINE = "Read the PDS and TMD for each option before deciding"
SENT_STAGES = {"client_decision", "paid", "signed", "bound", "declined"}
# (font size, max key terms per option, max chars per term, max chars per insurer name, max chars of recommendation text)
FITS = [(8.5, 4, 90, 80, 260), (7.6, 3, 70, 60, 220), (6.8, 3, 55, 48, 180), (6.0, 2, 40, 40, 150), (5.4, 1, 32, 32, 120)]


class NotReady(Exception):
    pass


def _aud(v) -> str:
    d = Decimal(str(v))
    return f"A${d:,.2f}"


def _cut(s: str, n: int) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: max(1, n - 1)].rstrip() + "…"


def _change(p: Decimal, old: Decimal) -> str:
    diff = p - old
    if diff == 0:
        return "No change"
    pct = f" ({'+' if diff > 0 else '−'}{abs(diff / old * 100).quantize(Decimal('0.1'))}%)" if old else ""
    return f"{'+' if diff > 0 else '−'}{_aud(abs(diff))}{pct}"


def _differences(o: dict, old_p: Decimal, old_e: Decimal) -> list[str]:
    p, e = Decimal(o["premium"]), Decimal(o["excess"])
    out = []
    if p != old_p:
        out.append(f"{_aud(abs(p - old_p))} {'more' if p > old_p else 'less'} a year than now")
    else:
        out.append("Same premium as now")
    out.append("Same excess" if e == old_e else f"Excess {_aud(abs(e - old_e))} {'higher' if e > old_e else 'lower'}")
    out.append("Same limits" if o["limits_same"] else "Limits differ from your current cover")
    return out


_NUM = re.compile(r"\d+(\.\d+)?")


def _limits(lim: dict) -> list[str]:
    return [f"{str(k).replace('_', ' ').capitalize()}: {_aud(v) if _NUM.fullmatch(str(v)) else v}" for k, v in (lim or {}).items()]


def _period(expiry) -> str:
    start = rn._utc(expiry)
    return f"{start.astimezone(SYD):%d/%m/%Y} to {(start + timedelta(days=365)).astimezone(SYD):%d/%m/%Y}"


async def content(store, tid: str, r: dict) -> dict:
    """Everything printed, as plain data. Raises NotReady until options have been sent to the client."""
    rec = r.get("recommendation") if rn.released(r.get("recommendation")) else None
    if not rec or rn.cur(r) not in SENT_STAGES:
        raise NotReady("Your renewal options aren't ready yet.")
    mode = await holidays.quoting_mode(store, tid)
    policy = await rn._policy(store, tid, r["policy_id"])
    comp = rn.comparison(r, policy)
    opts = [o for o in comp["options"] if not (mode == "manual_only" and o["mocked"])]  # real tenants: real/manual quotes only
    safe = wrap(store)
    async with use_tenant(tid):
        t = await safe.system("tenants").find_one({"tenant_id": tid}) or {}
        client = await safe.clients.find_one({"client_id": r["client_id"]}, {"_id": 0, "display_name": 1}) or {}
        shown = {o["insurer"].lower() for o in opts}
        docs = [d async for d in safe.doc_deliveries.find({"client_id": r["client_id"], "context": f"renewal:{r['renewal_id']}",
                                                           "kind": {"$in": ["PDS", "TMD"]}}, {"_id": 0}).sort([("insurer", 1), ("kind", 1)])]
        es = t.get("email_settings") or {}
        logo = await safe.brand_assets.find_one({"url": es["logo_url"]}, {"_id": 0}) if es.get("logo_url") else None
    by_ins: dict[str, list[str]] = {}
    for d in docs:
        if (d.get("insurer") or "").lower() in shown:
            by_ins.setdefault(d["insurer"], []).append(f"{d['kind']} v{d['version']}")
    old_p, old_e = Decimal(comp["expiring"]["premium"]), Decimal(comp["expiring"]["excess"])
    legal = t.get("legal_name") or t.get("display_name") or "Your broker"
    licence = t.get("afsl") or t.get("ar_number")
    contact = " · ".join(x for x in (es.get("sender_name") or legal, es.get("reply_to")) if x)
    return {
        "brand": {"legal_name": legal, "colour": es.get("brand_colour") or "#0F1F3D",
                  "logo": {"path": logo["storage_path"], "sha256": logo["sha256"]} if logo else None},
        "client": client.get("display_name") or r.get("client_name") or "—", "policy_class": r.get("product") or "—",
        "current_insurer": comp["expiring"]["insurer"] or "—", "period": _period(r["expiry"]),
        "current": {"premium": _aud(old_p), "excess": _aud(old_e), "terms": _limits(comp["expiring"]["limits"])},
        "options": [{"insurer": o["insurer"], "recommended": o["quote_id"] == rec["quote_id"], "demo": bool(o["mocked"]),
                     "premium": _aud(o["premium"]), "change": _change(Decimal(o["premium"]), old_p), "excess": _aud(o["excess"]),
                     "terms": _limits(o["limits"]) + [x for x in o["key_differences"] if x != "Limits differ from expiring"],
                     "differences": _differences(o, old_p, old_e)} for o in opts],
        "recommendation": " ".join([rec["text"]] + (["Reasons: " + "; ".join(rec["reasons"]) + "."] if rec.get("reasons") else [])),
        "warning": (rec.get("soa_note") if rec.get("tier") == "P" else rec.get("general_advice_warning"))
                   or rec.get("general_advice_warning") or rec.get("soa_note") or "",
        "docs": [f"{ins}: {', '.join(v)}" for ins, v in sorted(by_ins.items())],
        "contact": contact, "licence": f"AFSL/AR: {licence}" if licence else "AFSL/AR: [not set]",
        "prepared": f"Prepared by {legal} via brokAId — summary only; the policy wording prevails.",
    }


def _render(c: dict, generated: str, key: str, logo: bytes | None, fit: tuple) -> tuple[bytes, int]:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    fs, n_terms, term_len, ins_len, rec_len = fit
    brand = colors.HexColor(c["brand"]["colour"])
    base = ParagraphStyle("b", fontName="Helvetica", fontSize=fs, leading=fs * 1.25)
    bold = ParagraphStyle("bb", parent=base, fontName="Helvetica-Bold")
    head = ParagraphStyle("h", parent=bold, textColor=colors.white)
    small = ParagraphStyle("s", parent=base, fontSize=max(5, fs - 1.6), leading=max(5, fs - 1.6) * 1.25, textColor=colors.HexColor("#4A5568"))
    title = ParagraphStyle("t", parent=bold, fontSize=fs + 8, leading=(fs + 8) * 1.2, textColor=brand)
    P = lambda s, st=base: Paragraph(escape(str(s)), st)  # noqa: E731 — every printed string is escaped
    lines = lambda xs: Paragraph("<br/>".join(escape(_cut(x, term_len)) for x in xs[:n_terms]) or "—", base)  # noqa: E731
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=32, rightMargin=32, topMargin=28, bottomMargin=28,
                            title="Your renewal options", author=c["brand"]["legal_name"], invariant=1)
    top = [P(c["brand"]["legal_name"], ParagraphStyle("n", parent=bold, fontSize=fs + 2, textColor=brand))]
    if logo:
        img = Image(io.BytesIO(logo))
        img.drawWidth, img.drawHeight = img.drawWidth * 24 / img.drawHeight, 24
        if img.drawWidth > 200:
            img.drawWidth, img.drawHeight = 200, img.drawHeight * 200 / img.drawWidth
        top = [img] + top
    story = [Table([top], hAlign="LEFT", style=[("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]),
             Spacer(1, 6), P("Your renewal options", title), Spacer(1, 2),
             P(f"Client: {c['client']}  ·  Policy: {c['policy_class']}  ·  Current insurer: {c['current_insurer']}", base),
             P(f"Period of cover: {c['period']}  ·  Generated: {generated}", base), Spacer(1, 8)]
    opts = c["options"]
    hdr = [P("", head), P("Current cover", head)] + [
        P(("Option " + str(i + 1)) + (" — recommended" if o["recommended"] else ""), head) for i, o in enumerate(opts)]
    ins = [P("Insurer", bold), P(_cut(c["current_insurer"], ins_len), bold)] + [
        Paragraph(escape(_cut(o["insurer"], ins_len)) + (f"<br/><font color='#B45309'>{escape(DEMO_LABEL)}</font>" if o["demo"] else ""), bold)
        for o in opts]
    rows = [hdr, ins,
            [P("Annual premium", bold), P(c["current"]["premium"])] + [P(o["premium"], bold) for o in opts],
            [P("Change vs current", bold), P("—")] + [P(o["change"]) for o in opts],
            [P("Excess", bold), P(c["current"]["excess"])] + [P(o["excess"]) for o in opts],
            [P("Key limits / terms", bold), lines(c["current"]["terms"] or ["As per your current schedule"])] + [lines(o["terms"]) for o in opts],
            [P("Notable differences", bold), P("Your cover today")] + [lines(o["differences"]) for o in opts]]
    width = A4[0] - 64
    lab = 78
    cols = [lab] + [(width - lab) / (len(opts) + 1)] * (len(opts) + 1)
    style = [("BACKGROUND", (0, 0), (-1, 0), brand), ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E0")), ("BACKGROUND", (1, 1), (1, -1), colors.HexColor("#F7FAFC"))]
    for i, o in enumerate(opts):
        if o["recommended"]:
            style += [("BOX", (i + 2, 0), (i + 2, -1), 1.6, brand), ("BACKGROUND", (i + 2, 1), (i + 2, -1), colors.HexColor("#FFFBEB"))]
    story += [Table(rows, colWidths=cols, style=TableStyle(style), repeatRows=0), Spacer(1, 8),
              P("Your broker's recommendation", bold), P(_cut(c["recommendation"], rec_len)), Spacer(1, 6),
              P(c["warning"], base), Spacer(1, 4),
              Paragraph(escape(PDS_LINE) + ": " + escape("; ".join(c["docs"]) or "[not yet delivered]") + ".", base), Spacer(1, 10),
              P(f"Your broker: {c['contact']}  ·  {c['licence']}", small), P(c["prepared"], small),
              P(f"Content SHA-256 {key}", small)]
    doc.build(story)
    return buf.getvalue(), doc.page


def page_count(pdf: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page[^s]", pdf))


async def document(store, tid: str, r: dict, user_id: str) -> tuple[bytes, str]:
    """(pdf bytes, sha256). Rendered once per content version, then served from object storage."""
    c = await content(store, tid, r)
    key = hashlib.sha256(json.dumps({"v": RENDER_VERSION, **c}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    safe = wrap(store)
    async with use_tenant(tid):
        row = await safe.comparison_docs.find_one({"renewal_id": r["renewal_id"], "content_key": key}, {"_id": 0})
    if row:
        pdf, _ = await object_store.get_object(row["storage_path"])
    else:
        logo = (await object_store.get_object(c["brand"]["logo"]["path"]))[0] if c["brand"]["logo"] else None
        generated = f"{datetime.now(timezone.utc).astimezone(SYD):%d/%m/%Y}"
        for fit in FITS:
            pdf, pages = _render(c, generated, key, logo, fit)
            if pages == 1:
                break
        sha = hashlib.sha256(pdf).hexdigest()
        row = {"doc_id": str(uuid.uuid4()), "renewal_id": r["renewal_id"], "content_key": key, "sha256": sha, "pages": pages,
               "storage_path": f"tenants/{tid}/renewals/{r['renewal_id']}/comparison/{sha}.pdf", "generated": generated,
               "created_at": datetime.now(timezone.utc)}
        await object_store.put_object(row["storage_path"], pdf, "application/pdf")
        async with use_tenant(tid):
            await safe.comparison_docs.insert_one(dict(row))
    await rn._event(store, tid, "ComparisonDocumentGenerated",
                    {"renewal_id": r["renewal_id"], "envelope_id": r["envelope_id"], "sha256": row["sha256"],
                     "content_key": key, "user_id": user_id},
                    f"comparison:{r['renewal_id']}:{row['sha256']}:{user_id}", user_id, "client")
    return pdf, row["sha256"]
