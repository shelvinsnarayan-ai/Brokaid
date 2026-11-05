"""R1.9 — security middleware: headers, CSRF origin check on cookie-auth writes, rate limits (env-tunable)."""
from __future__ import annotations

import json
import os
import time
from collections import defaultdict, deque
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.auth.sessions import COOKIE_NAME

API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
HEADERS = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}
WRITE = {"POST", "PUT", "PATCH", "DELETE"}


def _origin(u: str) -> str:
    p = urlparse(u)
    return f"{p.scheme}://{p.netloc}" if p.scheme and p.netloc else ""


def allowed_origins() -> set[str]:
    out = {_origin(os.environ.get("APP_BASE_URL") or "")}
    out |= {_origin(o.strip()) for o in (os.environ.get("CORS_ORIGINS") or "").split(",") if o.strip() and o.strip() != "*"}
    out |= {_origin(o.strip()) for o in (os.environ.get("EXTRA_ALLOWED_ORIGINS") or "").split(",") if o.strip() and o.strip() != "*"}
    return {o for o in out if o}


def csrf_ok(method: str, headers, cookies) -> bool:
    """Cookie-auth state change: a browser always sends Origin (or Sec-Fetch-Site). Cross-site → refuse.
    Non-browser clients (no Origin, no Sec-Fetch-Site) can't carry a victim's cookie, so they pass."""
    if method not in WRITE or not cookies.get(COOKIE_NAME):
        return True
    origin = headers.get("origin") or _origin(headers.get("referer") or "")
    if origin:
        return origin in allowed_origins()
    return headers.get("sec-fetch-site") not in ("cross-site", "same-site")


def _limit(name: str, default: int) -> int:
    return int(os.environ.get(name) or default)


class _Window:
    def __init__(self):
        self.hits: dict[str, deque] = defaultdict(deque)

    def hit(self, key: str, limit: int, now: float) -> bool:
        q = self.hits[key]
        while q and q[0] <= now - 60:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


_WINDOW = _Window()
AUTH_PREFIXES = ("/api/auth/login", "/api/auth/password", "/api/auth/invite", "/api/auth/google", "/api/auth/mfa")


def client_ip(request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")


def has_operator_key(v, depth: int = 0) -> bool:
    """NoSQL operator injection: user JSON never legitimately carries '$'-prefixed keys."""
    if depth > 32:
        return True
    if isinstance(v, dict):
        return any(str(k).startswith("$") or has_operator_key(x, depth + 1) for k, x in v.items())
    if isinstance(v, list):
        return any(has_operator_key(x, depth + 1) for x in v)
    return False


def _operator_name(k: str) -> bool:
    return k.startswith("$") or "[$" in k  # `$ne=` or bracket notation `email[$ne]=`


def _operator_value(v) -> bool:
    """A form/query value that is itself JSON (e.g. a `meta` field) must not smuggle operators either."""
    if not isinstance(v, str) or "$" not in v or v.lstrip()[:1] not in ("{", "["):
        return False
    try:
        return has_operator_key(json.loads(v))
    except ValueError:
        return False


async def _has_operator(request) -> bool:
    if any(_operator_name(k) or _operator_value(v) for k, v in request.query_params.multi_items()):
        return True
    ct = request.headers.get("content-type") or ""
    if request.method not in WRITE:
        return False
    if "json" in ct:
        body = await request.body()  # cached by Starlette, replayed to the route
        if not body or b"$" not in body:
            return False
        try:
            return has_operator_key(json.loads(body))
        except ValueError:
            return False  # the route answers 422 for bad JSON
    if ct.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
        body = await request.body()  # cache first so the route can parse the form again
        if b"$" not in body and b"%24" not in body.upper():  # urlencoded forms carry `$` as %24
            return False
        form = await request.form()
        try:
            return any(_operator_name(k) or _operator_value(v) for k, v in form.multi_items())
        finally:
            await form.close()
    return False


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        if path.startswith("/api/") and not csrf_ok(request.method, request.headers, request.cookies):
            return self._harden(JSONResponse({"detail": "csrf_origin_rejected"}, status_code=403))
        if path.startswith("/api/") and await _has_operator(request):
            return self._harden(JSONResponse({"detail": "invalid_field_name"}, status_code=400))
        if path.startswith("/api/") and os.environ.get("RATE_LIMIT_DISABLED") != "1":
            ip, now = client_ip(request), time.monotonic()
            auth = path.startswith(AUTH_PREFIXES) and request.method in WRITE
            if auth and not _WINDOW.hit(f"auth:{ip}", _limit("RATE_LIMIT_AUTH_PER_MIN", 30), now):
                return self._harden(JSONResponse({"detail": "Too many sign-in attempts. Wait a minute."}, status_code=429,
                                                 headers={"Retry-After": "60"}))
            if not _WINDOW.hit(f"all:{ip}", _limit("RATE_LIMIT_PER_MIN", 1200), now):
                return self._harden(JSONResponse({"detail": "Too many requests. Wait a minute."}, status_code=429,
                                                 headers={"Retry-After": "60"}))
        return self._harden(await call_next(request))

    @staticmethod
    def _harden(resp):
        for k, v in HEADERS.items():
            resp.headers.setdefault(k, v)
        resp.headers.setdefault("Content-Security-Policy", API_CSP)
        return resp
