"""R1.9 — upload validation by magic bytes (never trust the declared content type)."""
from __future__ import annotations

SIGNATURES = (
    (b"%PDF-", "application/pdf"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"PK\x03\x04", "application/zip"),
)


def sniff(data: bytes) -> str | None:
    head = data[:16]
    for sig, mime in SIGNATURES:
        if head.startswith(sig):
            return mime
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[4:8] == b"ftyp" and head[8:12] in (b"heic", b"heix", b"mif1", b"msf1", b"heif"):
        return "image/heic"
    return None


ZIP_OFFICE = {"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


def matches(data: bytes, declared: str) -> bool:
    """The bytes are what the declared type claims (HEIC/HEIF treated alike; DOCX/XLSX are zip containers)."""
    real = sniff(data)
    if real is None:
        return False
    if declared in ("image/heic", "image/heif"):
        return real == "image/heic"
    if declared in ZIP_OFFICE:
        return real == "application/zip"
    return real == declared
