"""R3.8: pwa.service_worker check (production build, dev server, broken: SW served as HTML) and the built bundle's SW files."""
from __future__ import annotations

import os
import subprocess

import httpx
import pytest

from app.kernel import pwa

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BUILD_HTML = '<link rel="manifest" href="/manifest.webmanifest"><script id="vite-plugin-pwa:register-sw" src="/registerSW.js"></script>'
DEV_HTML = '<script id="vite-plugin-pwa:register-dev-sw" type="module">navigator.serviceWorker.register("/dev-sw.js?dev-sw")</script>'


def _site(html: str, broken: set[str] = frozenset()):
    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.raw_path.decode()
        if p.startswith("/c"):
            return httpx.Response(200, text=html, headers={"content-type": "text/html"})
        if p in broken:
            return httpx.Response(200, text="<!doctype html>", headers={"content-type": "text/html"})
        body = "importScripts('/push-handler.js')" if p in ("/sw.js", "/dev-sw.js?dev-sw") else "x"
        return httpx.Response(200, text=body, headers={"content-type": "text/javascript"})
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_service_worker_check_modes():
    ok = await pwa.check("https://app.example", _site(BUILD_HTML))
    assert ok["ok"] and ok["mode"] == "build"
    dev = await pwa.check("https://app.example", _site(DEV_HTML, broken={"/sw.js"}))  # dev server: /sw.js → HTML is fine
    assert dev["ok"] and dev["mode"] == "dev"
    bad = await pwa.check("https://app.example", _site(BUILD_HTML, broken={"/registerSW.js"}))
    assert not bad["ok"] and "/registerSW.js" in bad["detail"]
    none = await pwa.check("https://app.example", _site("<html></html>"))
    assert not none["ok"]


def test_built_bundle_has_service_worker():
    dist = os.path.join(ROOT, "frontend", "dist")
    if not os.path.exists(os.path.join(dist, "index.html")):
        pytest.skip("frontend not built (test_all builds it and runs ci/pwa_bundle_check.js after the build)")
    r = subprocess.run(["node", os.path.join(ROOT, "ci", "pwa_bundle_check.js"), dist], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
