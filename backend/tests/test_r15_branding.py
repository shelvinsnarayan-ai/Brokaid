"""R0.15 — brokerage branding editor: validation, contrast, logo rules, audit payload, roles, ledger (live API)."""
import io
import os
import uuid
from pathlib import Path

import httpx
import pytest
from dotenv import dotenv_values
from PIL import Image

from app.kernel import branding as br
from app.kernel import email_channel as ec

ROOT = Path(__file__).resolve().parents[2]
BASE = os.environ.get("REACT_APP_BACKEND_URL") or dotenv_values(ROOT / "frontend/.env").get("REACT_APP_BACKEND_URL")
OK = {"sender_name": "Harbourline Insurance Brokers", "reply_to": None, "brand_colour": "#0F1F3D", "logo_url": None,
      "allowlist": ["luka@underaiter.com"]}


def _png(w=800, h=160) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), "#0F1F3D").save(out, "PNG")
    return out.getvalue()


def _errs(body):
    with pytest.raises(br.BrandingInvalid) as e:
        br.validate(body, lambda _u: False)
    return e.value.errors


def test_contrast():
    assert br.contrast_with_white("#FFFFFF") == 1.0 and br.contrast_with_white("#000000") == 21.0
    assert br.contrast_with_white("#F2B01E") < 4.5 < br.contrast_with_white("#0F1F3D")


def test_validation_rules():
    clean = br.validate({**OK, "allowlist": [" Luka@UnderAIter.com", "*@Resend.dev", "luka@underaiter.com"],
                         "reply_to": "Help@Harbour.com.au", "brand_colour": "#0f1f3d"}, lambda _u: True)
    assert clean["allowlist"] == ["luka@underaiter.com", "*@resend.dev"] and clean["reply_to"] == "help@harbour.com.au"
    assert clean["brand_colour"] == "#0F1F3D"
    assert "sender_name" in _errs({**OK, "sender_name": "H"})
    assert "sender_name" in _errs({**OK, "sender_name": "x" * 81})
    for bad in ("Visit https://x.au", "www.harbour", "Harbour harbourline.com.au"):
        assert "web address" in _errs({**OK, "sender_name": bad})["sender_name"]
    for bad in ("a@b.example", "a@x.test", "a@y.invalid", "a@harbourline.example"):
        assert "reserved" in _errs({**OK, "reply_to": bad})["reply_to"]
    assert "valid email" in _errs({**OK, "reply_to": "nope"})["reply_to"]
    msg = _errs({**OK, "brand_colour": "#F2B01E"})["brand_colour"]
    assert "1.91:1" in msg and "4.5:1" in msg and "darker" in msg
    assert "brand_colour" in _errs({**OK, "brand_colour": "navy"})
    assert "logo_url" in _errs({**OK, "logo_url": "https://evil.example/x.png"})
    assert "allowlist" in _errs({**OK, "allowlist": ["not an email"]})


def test_audit_payload_hides_addresses():
    old = {**OK, "allowlist": ["a@x.com"]}
    new = {**OK, "sender_name": "Acme Brokers", "logo_url": "https://h/api/public/brand/1.png",
           "allowlist": ["a@x.com", "b@y.com"]}
    p = br.audit_payload(old, new, "abc")
    assert p["fields"] == ["sender_name", "logo_url", "allowlist"]
    assert p["sender_name"] == {"old": "Harbourline Insurance Brokers", "new": "Acme Brokers"}
    assert p["logo"]["new_sha256"] == "abc" and p["allowlist"]["old_count"] == 1 and p["allowlist"]["new_count"] == 2
    assert "b@y.com" not in str(p) and "a@x.com" not in str(p)
    assert br.audit_payload(old, {**old, "allowlist": ["A@x.com".lower()]}, None)["fields"] == []


def test_logo_processing():
    out, ct, ext, w, h = br.process_logo(_png(1200, 300))
    assert (ct, ext, w, h) == ("image/png", "png", 600, 150) and Image.open(io.BytesIO(out)).width == 600
    jpg = io.BytesIO()
    Image.new("RGB", (300, 100), "#123456").save(jpg, "JPEG")
    assert br.process_logo(jpg.getvalue())[1:4] == ("image/jpeg", "jpg", 300)
    for data, status, code in ((b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>', 422, "svg_not_allowed"),
                               (b"<svg/>", 422, "svg_not_allowed"), (b"GIF89a....", 422, "unsupported_type"),
                               (b"\x89PNG\r\n\x1a\n" + b"0" * 50, 422, "unreadable_image"),
                               (b"\x89PNG\r\n\x1a\n" + b"0" * (br.MAX_LOGO_BYTES + 1), 413, "logo_too_large")):
        with pytest.raises(br.LogoInvalid) as e:
            br.process_logo(data)
        assert (e.value.status, e.value.code) == (status, code)


def test_email_button_uses_brand_colour():
    s = ec.resolve_settings({"display_name": "Acme Brokers", "email_settings": {"brand_colour": "#1F5F4A"}})
    html = ec.render({"kind": "broker_review", "link": "/o/alerts"}, {"display_name": "Olly O"}, s)[1]
    assert "background:#1F5F4A" in html and "border-bottom:3px solid #1F5F4A" in html
    assert br.contrast_with_white(ec.DEFAULT_BRAND_COLOUR) >= br.MIN_CONTRAST


# ---- live API (roles, persistence, ledger, object storage) ----
live = pytest.mark.skipif(not BASE, reason="live API not configured")


def _as(email: str) -> httpx.Client:
    c = httpx.Client(base_url=BASE, timeout=60)
    assert c.post("/api/auth/dev/impersonate", json={"email": email}).status_code == 200
    return c


@live
def test_roles_and_round_trip():
    olly, ada, cass = _as("operator@harbourline.example"), _as("principal@harbourline.example"), _as("ae@harbourline.example")
    kai = _as("owner@fictionalclient.example")
    saved = olly.get("/api/o/tenant/branding").json()
    assert saved["can_edit"] is True and saved["tenant_name"] == "Harbourline Insurance Brokers"
    view = ada.get("/api/o/tenant/branding")
    assert view.status_code == 200 and view.json()["can_edit"] is False
    body = {**saved["settings"]}
    assert ada.put("/api/o/tenant/branding", json=body).status_code == 403
    for c in (cass, kai):
        assert c.get("/api/o/tenant/branding").status_code == 403 and c.put("/api/o/tenant/branding", json=body).status_code == 403
    assert olly.post("/api/o/tenant/branding/logo", files={"file": ("x.svg", b"<svg/>", "image/svg+xml")}).status_code == 422
    up = olly.post("/api/o/tenant/branding/logo", files={"file": ("l.png", _png(), "image/png")}).json()
    assert up["width"] == 600 and up["logo_url"].startswith(BASE) and "/api/public/brand/" in up["logo_url"]
    img = httpx.get(up["logo_url"], timeout=60)  # no cookies, like a mail app
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    name = f"Harbourline Pytest {uuid.uuid4().hex[:6]}"
    pv = olly.post("/api/o/tenant/branding/preview", json={"draft": {**body, "sender_name": name, "logo_url": up["logo_url"]}}).json()
    # emails only embed https logos; a plain-http test server's logo is dropped, never sent insecurely
    assert pv["mode"] == "draft" and name in pv["html"]
    assert (up["logo_url"] in pv["html"]) == up["logo_url"].startswith("https://")
    try:
        r = olly.put("/api/o/tenant/branding", json={**body, "sender_name": name, "logo_url": up["logo_url"]})
        assert r.status_code == 200 and set(r.json()["changed"]) == {"sender_name", "logo_url"}
        assert olly.get("/api/o/tenant/branding").json()["settings"]["sender_name"] == name
        assert olly.post("/api/o/tenant/branding/preview", json={}).json()["mode"] == "saved"
        trail = olly.get("/api/o/trail", params={"type": "BrandingSettingsChanged", "limit": 5})
        if trail.status_code == 200:
            assert "BrandingSettingsChanged" in trail.text
    finally:
        assert olly.put("/api/o/tenant/branding", json=body).status_code == 200  # restore
    assert olly.put("/api/o/tenant/branding", json=body).json()["changed"] == []  # no-op save writes no event
