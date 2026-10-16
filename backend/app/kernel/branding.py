"""R0.15 — per-brokerage email branding: validation, contrast, logo processing, audit payload."""
from __future__ import annotations

import hashlib
import io
import re

from email_validator import EmailNotValidError, validate_email
from PIL import Image

FIELDS = ("sender_name", "reply_to", "brand_colour", "logo_url", "allowlist")
MIN_CONTRAST = 4.5
MAX_LOGO_BYTES = 1024 * 1024
MAX_LOGO_WIDTH = 600
MAX_ALLOWLIST = 50
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_URLISH = re.compile(r"(https?:|www\.|//|\b[a-z0-9-]+\.(?:com|net|org|io|au|co|nz|uk|biz|info|app|dev|xyz)\b)", re.IGNORECASE)
_RESERVED = ("example", "test", "invalid", "localhost")


class BrandingInvalid(Exception):
    def __init__(self, errors: dict[str, str]):
        super().__init__(errors)
        self.errors = errors


class LogoInvalid(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(code)
        self.status, self.code, self.message = status, code, message


def _lum(hex_: str) -> float:
    def ch(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast_with_white(hex_: str) -> float:
    return round(1.05 / (_lum(hex_) + 0.05), 2)


def _reserved(host: str) -> bool:
    labels = host.lower().split(".")
    return any(lab in ("example",) for lab in labels) or labels[-1] in _RESERVED


def _email(v: str) -> str:
    return validate_email(v, check_deliverability=False).normalized.lower()


def validate(body: dict, logo_ok) -> dict:
    """Return the clean settings dict or raise BrandingInvalid({field: plain message})."""
    errs: dict[str, str] = {}
    name = " ".join((body.get("sender_name") or "").split())
    if not 2 <= len(name) <= 80:
        errs["sender_name"] = "Sender name must be 2 to 80 characters."
    elif _URLISH.search(name):
        errs["sender_name"] = "Sender name can't contain a web address."
    reply = (body.get("reply_to") or "").strip() or None
    if reply:
        host = reply.rsplit("@", 1)[-1] if "@" in reply else ""
        try:
            if host and _reserved(host):
                errs["reply_to"] = "Reply-to can't use a reserved test domain (.example, .test, .invalid)."
            else:
                reply = _email(reply)
        except EmailNotValidError:
            errs["reply_to"] = "Reply-to must be a valid email address."
    colour = (body.get("brand_colour") or "").strip()
    if not _HEX.match(colour):
        errs["brand_colour"] = "Brand colour must look like #0F1F3D."
    else:
        colour = colour.upper()
        ratio = contrast_with_white(colour)
        if ratio < MIN_CONTRAST:
            errs["brand_colour"] = (f"White button text on {colour} has a contrast of {ratio}:1. It needs at least "
                                    f"{MIN_CONTRAST}:1 so everyone can read it. Pick a darker colour.")
    logo = body.get("logo_url") or None
    if logo and not logo_ok(logo):
        errs["logo_url"] = "Upload the logo here first."
    allow: list[str] = []
    for raw in body.get("allowlist") or []:
        a = raw.strip().lower()
        if not a:
            continue
        try:
            a = "*@" + _email("x@" + a[2:]).split("@", 1)[1] if a.startswith("*@") else _email(a)
        except EmailNotValidError:
            errs["allowlist"] = f"\"{raw.strip()}\" isn't an email address or *@domain."
            break
        if a not in allow:
            allow.append(a)
    if len(allow) > MAX_ALLOWLIST:
        errs["allowlist"] = f"At most {MAX_ALLOWLIST} allow-list entries."
    if errs:
        raise BrandingInvalid(errs)
    return {"sender_name": name, "reply_to": reply, "brand_colour": colour, "logo_url": logo, "allowlist": allow}


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def audit_payload(old: dict, new: dict, logo_sha: str | None) -> dict:
    """Changed field names + old/new for plain fields; logo as URL + hash; allow-list as counts + hashes only."""
    norm = lambda d, f: sorted(d.get(f) or []) if f == "allowlist" else d.get(f)
    changed = [f for f in FIELDS if norm(old, f) != norm(new, f)]
    p: dict = {"fields": changed}
    for f in ("sender_name", "reply_to", "brand_colour"):
        if f in changed:
            p[f] = {"old": old.get(f), "new": new.get(f)}
    if "logo_url" in changed:
        p["logo"] = {"old_url": old.get("logo_url"), "new_url": new.get("logo_url"), "new_sha256": logo_sha}
    if "allowlist" in changed:
        o, n = norm(old, "allowlist"), norm(new, "allowlist")
        p["allowlist"] = {"old_count": len(o), "new_count": len(n),
                          "old_sha256": _sha("\n".join(o)), "new_sha256": _sha("\n".join(n))}
    return p


def process_logo(data: bytes) -> tuple[bytes, str, str, int, int]:
    """PNG/JPEG only, ≤ 1 MB, re-encoded and limited to 600px wide. Returns (bytes, content_type, ext, w, h)."""
    if len(data) > MAX_LOGO_BYTES:
        raise LogoInvalid(413, "logo_too_large", "The logo must be 1 MB or smaller.")
    head = data[:1024].lstrip().lower()
    if head.startswith((b"<?xml", b"<svg")) or b"<svg" in head:
        raise LogoInvalid(422, "svg_not_allowed", "SVG logos don't show in most email apps. Upload a PNG or JPG.")
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        fmt = "PNG"
    elif data[:3] == b"\xff\xd8\xff":
        fmt = "JPEG"
    else:
        raise LogoInvalid(422, "unsupported_type", "Upload a PNG or JPG.")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:
        raise LogoInvalid(422, "unreadable_image", "That image couldn't be read. Try another PNG or JPG.") from e
    if img.width * img.height > 25_000_000:
        raise LogoInvalid(422, "image_too_big", "That image is too large. Use one under 600px wide.")
    if img.width > MAX_LOGO_WIDTH:
        img = img.resize((MAX_LOGO_WIDTH, max(1, round(img.height * MAX_LOGO_WIDTH / img.width))), Image.LANCZOS)
    out = io.BytesIO()
    if fmt == "PNG":
        img.save(out, "PNG", optimize=True)
    else:
        img.convert("RGB").save(out, "JPEG", quality=88, optimize=True)
    return out.getvalue(), ("image/png" if fmt == "PNG" else "image/jpeg"), ("png" if fmt == "PNG" else "jpg"), img.width, img.height
