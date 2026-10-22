"""R3.8 — pwa.service_worker: the client app's service worker (web push, R1.3, depends on it) is served as JavaScript and
wired up. Production builds register /registerSW.js → /sw.js (which importScripts /push-handler.js). The Vite dev server
registers /dev-sw.js?dev-sw instead; its /sw.js falling back to HTML there is expected."""
from __future__ import annotations

import os
import time

import httpx

_CACHE: dict = {"at": 0.0, "res": None}
JS = "javascript"


async def check(base: str | None = None, transport: httpx.AsyncBaseTransport | None = None) -> dict:
    base = (base or os.environ.get("APP_BASE_URL") or "").rstrip("/")
    if not base:
        return {"ok": False, "detail": "APP_BASE_URL is not set, so the client app can't be checked."}
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True, transport=transport) as h:
            html = (await h.get(f"{base}/c")).text
            if "/registerSW.js" in html:
                mode, files = "build", ["/registerSW.js", "/sw.js", "/push-handler.js"]
            elif "register-dev-sw" in html or "dev-sw.js" in html:
                mode, files = "dev", ["/dev-sw.js?dev-sw", "/push-handler.js"]
            else:
                return {"ok": False, "mode": None, "detail": "The client page doesn't register a service worker, so web push can't work."}
            bad, sw = [], ""
            for p in files:
                r = await h.get(base + p)
                ct = r.headers.get("content-type", "")
                if r.status_code != 200 or JS not in ct:
                    bad.append(f"{p} → {r.status_code} {ct or 'no content type'}")
                elif p in ("/sw.js", "/dev-sw.js?dev-sw"):
                    sw = r.text
            if not bad and mode == "build" and "push-handler.js" not in sw:
                bad.append("/sw.js doesn't import /push-handler.js")
    except httpx.HTTPError as e:
        return {"ok": False, "mode": None, "detail": f"Couldn't reach the client app ({type(e).__name__})."}
    if bad:
        return {"ok": False, "mode": mode, "detail": "Service worker files aren't served as JavaScript: " + "; ".join(bad)}
    return {"ok": True, "mode": mode, "detail": f"Service worker served as JavaScript ({'production build' if mode == 'build' else 'dev server'})."}


async def cached(ttl: float = 60.0) -> dict:
    if _CACHE["res"] is None or time.monotonic() - _CACHE["at"] > ttl:
        _CACHE["res"], _CACHE["at"] = await check(), time.monotonic()
    return _CACHE["res"]
