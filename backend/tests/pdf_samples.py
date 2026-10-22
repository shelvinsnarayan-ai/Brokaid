"""R3.6 test samples: small PDFs built on the fly (clean, with JavaScript, encrypted, hex-escaped and Flate-hidden scripts)."""
from __future__ import annotations

import io
import zlib


def clean_pdf(title: str = "Quote Q-1042") -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.drawString(72, 760, title)
    c.save()
    return buf.getvalue()


def _raw(objs: list[bytes]) -> bytes:
    out = b"%PDF-1.7\n"
    for i, o in enumerate(objs, 1):
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    return out + b"trailer\n<< /Root 1 0 R >>\n%%EOF\n"


def js_pdf() -> bytes:
    return _raw([b"<< /Type /Catalog /Pages 2 0 R /OpenAction << /S /JavaScript /JS (app.alert(1)) >> >>",
                 b"<< /Type /Pages /Kids [] /Count 0 >>"])


def hex_js_pdf() -> bytes:  # /JavaScript written with #xx name escapes
    return _raw([b"<< /Type /Catalog /OpenAction << /S /J#61vaScript /J#53 (x) >> >>"])


def hidden_js_pdf() -> bytes:  # script inside a Flate-compressed object stream
    data = zlib.compress(b"<< /S /JavaScript /JS (app.alert(1)) >>")
    return _raw([b"<< /Type /Catalog >>", b"<< /Type /ObjStm /Filter /FlateDecode /Length %d >>\nstream\n" % len(data) + data + b"\nendstream"])


def encrypted_pdf() -> bytes:
    return _raw([b"<< /Type /Catalog >>"]).replace(b"<< /Root 1 0 R >>", b"<< /Root 1 0 R /Encrypt << /Filter /Standard /V 2 >> >>")


def launch_pdf() -> bytes:
    return _raw([b"<< /Type /Catalog /OpenAction << /S /Launch /F (cmd.exe) >> >>"])


def embedded_pdf() -> bytes:
    return _raw([b"<< /Type /Catalog /Names << /EmbeddedFiles << /Names [] >> >> >>"])
