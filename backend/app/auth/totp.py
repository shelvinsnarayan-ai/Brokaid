"""TOTP enrolment + verification. Secret is base32; QR is a plain SVG string."""
from __future__ import annotations

import io

import pyotp
import qrcode
import qrcode.image.svg


ISSUER = "brokAId"


def new_secret() -> str:
    return pyotp.random_base32()


def otpauth_uri(secret: str, account: str, issuer: str = ISSUER) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=issuer)


def qr_svg(uri: str) -> str:
    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage)  # viewBox + plain <path>: renders inline in HTML
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")


def verify_code(secret: str, code: str) -> bool:
    if not secret or not code:
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def current_code(secret: str) -> str:
    """Helper for tests: return the current TOTP code."""
    return pyotp.TOTP(secret).now()
