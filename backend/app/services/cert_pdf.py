"""Server-side certificate PDF renderer — Meridian light expression.

Deterministic layout (portrait A4). Palette locked to
identity_kit/palette_and_contrast.json:
  Cloud  #F6F7FA (background)
  Navy   #0F1F3D (text)
  Steel  #3E6D9C (secondary text)
  Saffron#F2B01E (accent rule)
White for cards.

MOCKED · CARRIER pill is embedded next to the insurer field per the
brand rule ("Saffron and steel carry attention, never information").
No agent internal names appear.
"""
from __future__ import annotations

import io
from datetime import datetime
from typing import Any

import qrcode
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


NAVY = (15 / 255, 31 / 255, 61 / 255)
STEEL = (62 / 255, 109 / 255, 156 / 255)
SAFFRON = (242 / 255, 176 / 255, 30 / 255)
CLOUD = (246 / 255, 247 / 255, 250 / 255)
WHITE = (1, 1, 1)


def _qr_png(url: str) -> bytes:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8, border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0F1F3D", back_color="#F6F7FA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _fmt_money(v, currency: str = "AUD") -> str:
    try:
        return f"{currency} ${float(v):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%d/%m/%Y")
    except Exception:
        return iso


def _fmt_datetime(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%d/%m/%Y · %H:%M UTC")
    except Exception:
        return iso


def _interest_label(v: str | None) -> str:
    return {
        "additional_insured": "Additional insured",
        "interested_party": "Interested party",
        "loss_payee": "Loss payee",
    }.get((v or "").lower(), "—")


def render_certificate_pdf(
    *, certificate: dict[str, Any], verify_url: str,
    ledger_event_id: str | None = None,
) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    page_w, page_h = A4

    # Background fill
    c.setFillColorRGB(*CLOUD)
    c.rect(0, 0, page_w, page_h, stroke=0, fill=1)

    margin = 18 * mm
    y = page_h - margin

    # --- Wordmark
    c.setFillColorRGB(*NAVY)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(margin, y - 4, "brok")
    c.setFillColorRGB(*SAFFRON)
    c.drawString(margin + 34, y - 4, "AI")
    c.setFillColorRGB(*NAVY)
    c.drawString(margin + 56, y - 4, "d")
    c.setFont("Helvetica", 9)
    c.setFillColorRGB(*STEEL)
    c.drawRightString(page_w - margin, y - 4, "CERTIFICATE OF CURRENCY")

    y -= 12 * mm
    c.setStrokeColorRGB(*SAFFRON)
    c.setLineWidth(2)
    c.line(margin, y, margin + 40 * mm, y)

    # --- Title
    y -= 12 * mm
    c.setFillColorRGB(*NAVY)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(margin, y, "Certificate of currency")
    y -= 6 * mm
    c.setFont("Helvetica", 10)
    c.setFillColorRGB(*STEEL)
    c.drawString(
        margin, y,
        "Issued by Harbourline Insurance Brokers Pty Ltd (fictional) on behalf of the insured.",
    )

    # --- Insurer + MOCKED pill
    y -= 14 * mm
    _label_value(c, margin, y, "Insurer",
                 certificate.get("insurer_display") or "—", value_size=13)
    # MOCKED · CARRIER pill (right of label)
    pill_x = margin + 82 * mm
    pill_y = y + 1
    c.setFillColorRGB(*SAFFRON)
    c.roundRect(pill_x, pill_y, 34 * mm, 5.5 * mm, 2.5 * mm, stroke=0, fill=1)
    c.setFillColorRGB(*NAVY)
    c.setFont("Helvetica-Bold", 7.5)
    c.drawString(pill_x + 3 * mm, pill_y + 1.6 * mm, "MOCKED · CARRIER")

    # --- Product + Issued
    y -= 14 * mm
    _label_value(c, margin, y, "Product",
                 certificate.get("product") or "—")
    _label_value(c, margin + 90 * mm, y, "Issued",
                 _fmt_datetime(certificate.get("issued_at")))

    # --- Holder
    y -= 14 * mm
    holder = certificate.get("holder") or {}
    holder_name = holder.get("name") or certificate.get("holder_display") or "—"
    holder_addr = holder.get("address") or ""
    holder_email = holder.get("email") or ""
    _label_value(c, margin, y, "Certificate holder", holder_name, value_size=12)
    if holder_addr:
        y -= 5 * mm
        c.setFont("Helvetica", 10)
        c.setFillColorRGB(*NAVY)
        c.drawString(margin, y, holder_addr[:120])
    if holder_email:
        y -= 4.5 * mm
        c.setFont("Helvetica", 10)
        c.setFillColorRGB(*STEEL)
        c.drawString(margin, y, holder_email)

    # --- Requirements
    y -= 14 * mm
    req = certificate.get("requirements") or {}
    _label_value(c, margin, y, "Interest",
                 _interest_label(req.get("interest_type")))
    _label_value(c, margin + 60 * mm, y, "Effective from",
                 _fmt_date(certificate.get("effective_from")))
    _label_value(c, margin + 120 * mm, y, "Effective to",
                 _fmt_date(certificate.get("effective_to")))

    # --- Cover types
    y -= 14 * mm
    c.setFont("Helvetica", 8)
    c.setFillColorRGB(*STEEL)
    c.drawString(margin, y, "COVER")
    y -= 5 * mm
    c.setFont("Helvetica", 11)
    c.setFillColorRGB(*NAVY)
    for ct in (certificate.get("cover_types") or []) or []:
        if not isinstance(ct, dict):
            continue
        line = f"• {ct.get('type', 'Cover')} — limit any one occurrence {_fmt_money(ct.get('limit_any_one'), ct.get('currency') or 'AUD')}"
        c.drawString(margin, y, line[:110])
        y -= 5 * mm
    if not certificate.get("cover_types"):
        c.setFillColorRGB(*STEEL)
        c.drawString(margin, y, "See policy schedule for full cover terms.")
        y -= 5 * mm

    # --- Vehicles (motor policies only)
    vehicles = certificate.get("vehicles") or []
    if vehicles:
        y -= 6 * mm
        c.setFont("Helvetica", 8)
        c.setFillColorRGB(*STEEL)
        c.drawString(margin, y, "VEHICLES")
        y -= 5 * mm
        c.setFont("Helvetica", 11)
        c.setFillColorRGB(*NAVY)
        for v in vehicles:
            desc = " ".join(str(x) for x in (v.get("year"), v.get("make"), v.get("model")) if x)
            line = f"• {v.get('rego') or '—'} · {desc or '—'} · primary driver {v.get('driver_assigned') or '—'}"
            c.drawString(margin, y, line[:110])
            y -= 5 * mm

    # --- Special requirements
    special = (req.get("special_requirements") or "").strip()
    if special:
        y -= 6 * mm
        c.setFont("Helvetica", 8)
        c.setFillColorRGB(*STEEL)
        c.drawString(margin, y, "SPECIAL REQUIREMENTS")
        y -= 5 * mm
        c.setFont("Helvetica", 10)
        c.setFillColorRGB(*NAVY)
        for chunk in _wrap(special, 90):
            c.drawString(margin, y, chunk)
            y -= 4.5 * mm

    # --- QR + verifier URL (bottom-right)
    qr_size = 44 * mm
    qr_x = page_w - margin - qr_size
    qr_y = margin + 22 * mm
    qr_bytes = _qr_png(verify_url)
    c.drawImage(
        ImageReader(io.BytesIO(qr_bytes)),
        qr_x, qr_y, width=qr_size, height=qr_size,
        preserveAspectRatio=True, mask="auto",
    )
    c.setFont("Helvetica", 8)
    c.setFillColorRGB(*STEEL)
    c.drawRightString(page_w - margin, qr_y - 4 * mm, "Scan to verify")
    c.setFont("Helvetica", 7.5)
    c.drawRightString(page_w - margin, qr_y - 7.5 * mm, verify_url[:80])

    # --- Footer
    c.setFillColorRGB(*NAVY)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(margin, margin + 8 * mm, "Meridian · Harbour")
    c.setFont("Helvetica", 7.5)
    c.setFillColorRGB(*STEEL)
    if ledger_event_id:
        c.drawString(
            margin, margin + 4 * mm,
            f"Ledger event {ledger_event_id[:36]} · certificate {certificate.get('certificate_id', '')[:36]}",
        )
    else:
        c.drawString(
            margin, margin + 4 * mm,
            f"Certificate {certificate.get('certificate_id', '')[:36]}",
        )
    c.drawString(
        margin, margin + 1 * mm,
        "Insurer response mocked for demo. Placement subject to insurer confirmation.",
    )

    c.showPage()
    c.save()
    return buf.getvalue()


def _label_value(c, x, y, label, value, *, value_size=12):
    c.setFont("Helvetica", 8)
    c.setFillColorRGB(*STEEL)
    c.drawString(x, y + 5.5 * mm, label.upper())
    c.setFont("Helvetica-Bold", value_size)
    c.setFillColorRGB(*NAVY)
    c.drawString(x, y, (value or "—")[:60])


def _wrap(text: str, cols: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    line = ""
    for w in words:
        if not line:
            line = w
        elif len(line) + 1 + len(w) <= cols:
            line = f"{line} {w}"
        else:
            lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines
