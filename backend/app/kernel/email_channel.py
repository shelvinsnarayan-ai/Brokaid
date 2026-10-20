"""R0.12 Phase B — email channel (Emergent-managed Resend proxy) + quiet-hours sender loop.

At creation the channel only marks a row `pending` (or `skipped:off`); `run_once` does the sending,
so in-app delivery and the ledger never wait on email. One email per notification row.
Statuses in channels_state.email: pending | held | sending | sent | failed | skipped:<reason>.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import ipaddress
from datetime import datetime, time, timedelta, timezone
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

from app.kernel import inbox

log = logging.getLogger("brokaid.email")
EMAIL_BASE_URL = "https://integrations.emergentagent.com"
SEND_EVERY_S = 60
SETTLE = timedelta(seconds=60)          # let 5-minute coalescing gather before sending
BACKOFF = [timedelta(seconds=30), timedelta(minutes=2), timedelta(minutes=10)]
MAX_ATTEMPTS = 3
QUIET_START, QUIET_END = time(21, 0), time(7, 0)

COPY = {  # fixed server-side templates; no client names, premiums or policy numbers (G4 + brief)
    "client_question": ("New client question", "A client has asked a question. It needs a reply within one business day."),
    "broker_reply": ("Your broker replied", "Your broker has replied to your question."),
    "broker_review": ("A request needs a broker", "A client request is waiting for broker review."),
    "approval_needed": ("Ready for your approval", "Your broker has checked your change. Please review and approve it."),
    "signature_needed": ("Your acceptance is needed", "Your change is approved. Please accept it to finish."),
    "certificate_issued": ("Your certificate is ready", "A new certificate of currency is ready to view."),
    "broker_reviewed": ("Your broker has reviewed your request", "Your broker will contact you about the next step."),
    "promise_due": ("A client reply is due soon", "A reply you promised a client is due within two hours."),
    "promise_overdue": ("A client reply is overdue", "A reply promised to a client is now overdue."),
    "renewal_30": ("A renewal is coming up", "A policy renews within the next 30 days."),
}


class EmailChannel:
    name = "email"

    async def deliver(self, n: dict, prefs: dict) -> str:
        if not email_enabled():
            return "skipped:email_disabled"
        return "pending" if prefs.get("email") else "skipped:off"


def email_enabled() -> bool:
    """EMAIL_ENABLED wins when set; otherwise off in production, on elsewhere (R0.14)."""
    v = (os.environ.get("EMAIL_ENABLED") or "").strip().lower()
    if v:
        return v == "true"
    from app.kernel import devmode
    return not devmode.is_production()


DEFAULT_BRAND_COLOUR = "#0B1B3A"  # button background; white text passes 4.5:1
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_RASTER = (".png", ".jpg", ".jpeg", ".gif")


def resolve_settings(tenant: dict) -> dict:
    """Per-brokerage email settings from the tenant record; env is only the fallback default."""
    s = tenant.get("email_settings") or {}
    allow = s.get("allowlist")
    return {
        "sender_name": s.get("sender_name") or tenant.get("display_name") or os.environ.get("EMAIL_FROM_NAME") or "",
        "reply_to": s.get("reply_to") or os.environ.get("EMAIL_REPLY_TO") or None,
        "brand_colour": s["brand_colour"] if _HEX.match(s.get("brand_colour") or "") else DEFAULT_BRAND_COLOUR,
        "logo_url": s.get("logo_url") or None,
        "allowlist": [a.strip().lower() for a in allow if a.strip()] if allow is not None else _allowlist(),
        "source": "tenant" if s else "env",
    }


async def tenant_settings(store, tenant_id: str) -> dict:
    # tenant-safe: system-only — one tenant record by its own id.
    return resolve_settings(await store.tenants.find_one({"tenant_id": tenant_id}) or {})


def in_quiet_hours(now: datetime) -> bool:
    t = now.astimezone(inbox.SYD).time()
    return t >= QUIET_START or t < QUIET_END


def next_send_time(now: datetime) -> datetime:
    local = now.astimezone(inbox.SYD)
    day = local.date() if local.time() < QUIET_END else local.date() + timedelta(days=1)
    return datetime.combine(day, QUIET_END, tzinfo=inbox.SYD).astimezone(timezone.utc)


def _allowlist() -> list[str]:
    return [a.strip().lower() for a in os.environ.get("EMAIL_ALLOWLIST", "").split(",") if a.strip()]


def allowed(addr: str, allow: list[str]) -> bool:
    a = addr.lower()
    return any(a == p or (p.startswith("*@") and a.endswith(p[1:])) for p in allow)


ROLE_NAMES = frozenset({"client", "client bookkeeper", "account executive", "principal", "operator", "sandbox principal"})


def greet_name(display_name: str | None) -> str:
    """R3.0: "Hi there" for role-named demo personas (and blanks); the first name for a real person."""
    n = (display_name or "").strip()
    return "there" if not n or n.lower() in ROLE_NAMES else n.split(" ")[0]


def resolve_address(user: dict, allow: list[str]) -> tuple[str | None, str | None]:
    addr = (user.get("email_override") or user.get("email") or "").strip()
    host = addr.rsplit("@", 1)[-1].lower() if "@" in addr else ""
    if not host:
        return None, "skipped:no_address"
    labels = host.split(".")
    if "example" in labels or labels[-1] in ("test", "invalid", "localhost"):  # RFC 2606 reserved
        return None, "skipped:example_address"
    if not allowed(addr, allow):
        return None, "skipped:not_allowlisted"
    return addr, None


def render(n: dict, user: dict, settings: dict) -> tuple[str, str, str]:
    subject, line = COPY.get(n["kind"], ("You have a new alert", "There is something new for you."))
    brand, colour, base = settings["sender_name"], settings["brand_colour"], os.environ["APP_BASE_URL"].rstrip("/")
    url = base + n["link"]
    logo = settings.get("logo_url") or ""
    logo = logo if logo.startswith("https://") else (base + logo if logo.startswith("/") else "")
    head = (f'<img src="{escape(logo)}" alt="{escape(brand)}" height="28" style="display:block;border:0">'
            if logo.lower().endswith(_RASTER) else escape(brand))  # SVG logos don't render in most mail apps
    first = escape(greet_name(user.get("display_name")))
    text = (f"Hi {first},\n\n{line}\n\nOpen {brand}: {url}\n\n"
            f"Turn these emails off in You → Notifications.\nSent by {brand}. We never ask for passwords or card details by email.")
    html = (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F4F6FA;padding:24px 0">'
        '<tr><td align="center"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="max-width:560px;background:#FFFFFF;border-radius:12px;font-family:Arial,Helvetica,sans-serif;color:#0B1B3A">'
        f'<tr><td style="padding:20px 28px;border-bottom:3px solid {colour};font-size:15px;font-weight:bold">{head}</td></tr>'
        f'<tr><td style="padding:24px 28px 8px;font-size:20px;font-weight:bold">{escape(subject)}</td></tr>'
        f'<tr><td style="padding:0 28px 20px;font-size:15px;line-height:22px">Hi {first},<br>{escape(line)}</td></tr>'
        f'<tr><td style="padding:0 28px 28px"><a href="{escape(url)}" style="display:inline-block;background:{colour};'
        'color:#FFFFFF;text-decoration:none;padding:12px 22px;border-radius:999px;font-size:14px;font-weight:bold">'
        'Open the app</a></td></tr>'
        '<tr><td style="padding:16px 28px;border-top:1px solid #E3E8F0;font-size:12px;color:#5B6B85">'
        f'Turn these emails off in You → Notifications.<br>Sent by {escape(brand)}. '
        'We never ask for passwords or card details by email.</td></tr></table></td></tr></table>')
    return subject, html, text


# ---- guardrail gate (verbatim from the integration playbook; never weaken) ----
_SHORTENERS = ("bit.ly", "tinyurl.com", "t.co", "is.gd", "cutt.ly", "goo.gl", "rebrand.ly")
_CRED_ASK = ("reply with your password", "reply with the code", "send your password", "cvv",
             "send us your password", "enter your password below", "confirm your card number",
             "your full card number", "seed phrase", "recovery phrase", "verify your card",
             "social security number", "confirm your bank details")
_HOSTISH = re.compile(r"\b(?:https?://)?((?:[a-z0-9-]+\.)+[a-z]{2,})", re.I)


def _host_ok(host: str) -> bool:
    if not host or "xn--" in host:
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    return not any(host == s or host.endswith("." + s) for s in _SHORTENERS)


def _same_site(shown: str, real: str) -> bool:
    return shown == real or real.endswith("." + shown) or shown.endswith("." + real)


class _EmailScan(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.urls, self.anchors = set(), [], []
        self._href, self._text = None, []

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag.lower())
        self.urls += [v for k, v in attrs if k.lower() in ("href", "src") and v]
        if tag.lower() == "a":
            self._href = dict((k.lower(), v) for k, v in attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href is not None:
            self.anchors.append((self._href, "".join(self._text)))
            self._href, self._text = None, []


def _assert_safe_email(subject: str, html: str) -> None:
    scan = _EmailScan()
    scan.feed(html)
    if scan.tags & {"form", "input", "textarea", "select"}:
        raise ValueError("No forms or input fields in email (G2)")
    body = f"{subject}\n{html}".lower()
    for p in _CRED_ASK:
        if p in body:
            raise ValueError(f"Email asks the recipient for credentials: {p!r} (G2)")
    for url in scan.urls:
        low = url.strip().lower()
        if low.startswith(("mailto:", "tel:", "cid:", "#")):
            continue
        if not low.startswith("https://"):
            raise ValueError(f"Email links/assets must be absolute https: {url!r} (G3)")
        host = urlparse(low).hostname or ""
        if not _host_ok(host) or urlparse(low).username is not None:
            raise ValueError(f"Shortened, numeric-host or credential-bearing URL: {url!r} (G3)")
    for href, text in scan.anchors:
        real = urlparse(href.strip().lower()).hostname or ""
        if not real:
            continue
        for m in _HOSTISH.finditer(text):
            if not _same_site(m.group(1).lower(), real):
                raise ValueError(f"Anchor text {m.group(1)!r} ≠ real link host {real!r} (G3)")


class BillingPaused(Exception):
    pass


async def resend_send(*, to: str, subject: str, html: str, text: str, from_name: str,
                      reply_to: str | None = None, attachments: list[dict] | None = None) -> str:
    _assert_safe_email(subject, html)
    payload = {"to": [to], "subject": subject, "html": html, "text": text, "from_name": from_name}
    if reply_to:
        payload["contact_email"] = reply_to
    if attachments:  # Resend format: [{"filename", "content" (base64)}]
        payload["attachments"] = [{"filename": a["filename"], "content": a["content"]} for a in attachments]
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{EMAIL_BASE_URL}/api/v1/email/send",
                                 headers={"X-Email-Key": os.environ["EMERGENT_EMAIL_KEY"]}, json=payload)
    if resp.status_code in (402, 403, 409):
        code = (resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}).get("code", "")
        if code in ("insufficient_credits", "integration_disabled", "daily_limit_reached"):
            log.warning("Email paused: %s (%s)", code, resp.status_code)
            raise BillingPaused(code)
    resp.raise_for_status()
    return resp.json().get("id")


async def run_once(store, now: datetime | None = None, *, send=None, allow: list[str] | None = None,
                   ids: list[str] | None = None, ignore_quiet: bool = False, settings_override: dict | None = None) -> dict:
    """Process due email rows. Injected clock/sender/allow-list for tests; settings_override for draft previews."""
    # tenant-safe: system-only — the sender spans tenants; every row is handled by its own id.
    now, send = now or datetime.now(timezone.utc), send or resend_send
    q: dict = {"channels_state.email": {"$in": ["pending", "held", "retry"]},
               "updated_at": {"$lte": now - SETTLE}, "$or": [{"email_next_at": None}, {"email_next_at": {"$lte": now}},
                                                             {"email_next_at": {"$exists": False}}]}
    if ids:
        q["notification_id"] = {"$in": ids}
    else:  # background loop never sends test rows; explicit test sends pass ids
        q["test"] = {"$ne": True}
    from app.kernel.platform import hold_suspended
    q["tenant_id"] = {"$nin": await hold_suspended(store, "notifications", "channels_state.email")}  # R2.6
    out: dict = {}
    async for n in store.notifications.find(q).limit(200):
        status = await _process(store, n, now, send, allow, ignore_quiet, settings_override)
        out[status] = out.get(status, 0) + 1
    return out


async def _set(store, n, status, **extra):
    await store.notifications.update_one({"_id": n["_id"]}, {"$set": {"channels_state.email": status, **extra}})
    return status


async def _process(store, n, now, send, allow, ignore_quiet=False, settings_override=None) -> str:
    if not email_enabled():
        return await _set(store, n, "skipped:email_disabled")
    if n.get("read_at"):
        return await _set(store, n, "skipped:read_in_app")
    user = await store.users.find_one({"user_id": n["user_id"], "tenant_id": n["tenant_id"]}) or {}
    if user.get("disabled"):  # R2.6: never email a disabled user
        return await _set(store, n, "skipped:user_disabled")
    prefs = await store.notification_prefs.find_one({"user_id": n["user_id"], "tenant_id": n["tenant_id"]}) or {}
    if not prefs.get("email", user.get("kind") == "operator"):
        return await _set(store, n, "skipped:off")
    settings = settings_override or await tenant_settings(store, n["tenant_id"])
    addr, why = resolve_address(user, settings["allowlist"] if allow is None else allow)
    if why:
        return await _set(store, n, why)
    if in_quiet_hours(now) and not ignore_quiet:
        return await _set(store, n, "held", email_next_at=next_send_time(now))
    claimed = await store.notifications.update_one(
        {"_id": n["_id"], "channels_state.email": n["channels_state"]["email"]}, {"$set": {"channels_state.email": "sending"}})
    if not claimed.modified_count:
        return "raced"
    subject, html, text = render(n, user, settings)
    attempts = n.get("email_attempts", 0) + 1
    try:
        mid = await send(to=addr, subject=subject, html=html, text=text, from_name=settings["sender_name"],
                         reply_to=settings["reply_to"])
        return await _set(store, n, "sent", email_message_id=mid, email_sent_at=now, email_attempts=attempts)
    except Exception as e:  # noqa: BLE001 — never blocks in-app or the ledger
        log.warning("email send failed (attempt %s): %s", attempts, _scrubbed(e))  # no recipient/PII in logs
        if attempts >= MAX_ATTEMPTS:
            return await _set(store, n, "failed", email_attempts=attempts, email_error=str(e)[:200])
        return await _set(store, n, "retry", email_attempts=attempts, email_next_at=now + BACKOFF[attempts - 1],
                          email_error=str(e)[:200])


def _scrubbed(e: Exception) -> str:
    from app.kernel.ops import scrub
    return f"{type(e).__name__}: {scrub(str(e))[:200]}"


async def loop(store) -> None:
    while True:
        try:
            await run_once(store)
        except Exception as e:  # noqa: BLE001
            log.warning("email sender failed (non-fatal): %s", _scrubbed(e))
        await asyncio.sleep(SEND_EVERY_S)
