"""R0.6 deny-list (Addendum 2 §9) — rendered-DOM sweep of client + broker routes.

Logs in via dev impersonation, renders each route in Chromium, scans the
visible text. Reports route + offending text + likely source file:line.
Usage: python ci/deny_list_dom.py [--base URL]   (exit 1 on any violation)
"""
import argparse
import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
COMMON = [
    ("HITL", r"\bHITL\b"), ("predicate", r"\bpredicate\b"), ("envelope", r"\benvelope\b"),
    ("scalar", r"\bscalar\b"), ("class_key", r"class_key"), ("snake_case", r"\b[a-z]+_[a-z][a-z_]*\b"),
    ("agent name", r"\b(intAIke|plAIce|clAIms|complAInce|liAIson)\b"), ("model id", r"claude-sonnet|gpt-\d"),
    ("JSON braces", r"[{}]"), ("coming post-", r"coming post-"), ("later phase", r"later phase"),
    ("awaits phase", r"awaits phase"), ("negative duration", r"[-−]\d+\s?h\b"), ("BurMac", r"Bur[Mm]ac"),
]
CLIENT_ONLY = [("MOCKED", r"\bMOCKED\b")]
AE_ONLY = [("confidence decimal", r"\b[01]\.\d{2,3}\b")]
MOTOR = "00000000-0000-4000-8000-000000000104"
CLIENT_ROUTES = ["/c", "/c/cover", "/c/money", "/c/claims", "/c/ask", "/c/profile", "/c/alerts",
                 f"/c/cover/{MOTOR}/certificate/new", f"/c/cover/{MOTOR}/request-change", "/c/claims/new"]
AE_ROUTES = ["/o/queue", "/o/clients", "/o/renewals", "/o/inbox", "/o/inbox/seed-ask-kai-2", "/o/alerts", "/o/invites", "/account/security"]
PUBLIC_ROUTES = ["/login", "/invite/not-a-real-token", "/reset/not-a-real-token"]


def source_hint(text: str) -> str:
    needle = text.strip()[:40]
    if len(needle) < 4:
        return "?"
    r = subprocess.run(["grep", "-rnF", needle, str(ROOT / "frontend/src")], capture_output=True, text=True)
    first = r.stdout.splitlines()[:1]
    return first[0].split(":", 2)[0].replace(str(ROOT) + "/", "") + ":" + first[0].split(":", 2)[1] if first else "(data-driven)"


def scan(route: str, text: str, rules) -> list[str]:
    out = []
    for line in text.splitlines():
        for name, rx in rules:
            m = re.search(rx, line)
            if m:
                out.append(f"DENY {name}: {route} → {line.strip()[:120]!r} [{source_hint(line)}]")
    return out


async def sweep(base: str) -> list[str]:
    hits: list[str] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(viewport={"width": 390, "height": 844})  # R1.2a: signed-out auth pages
        page = await ctx.new_page()
        for route in PUBLIC_ROUTES:
            await page.goto(f"{base}{route}", wait_until="load")
            await page.wait_for_timeout(2500)
            body = await page.inner_text("body")
            if len(body) < 30:
                hits.append(f"DENY empty render: {route} rendered no text")
            print(f"  scanned {route} signed out: {len(body)} chars", file=sys.stderr)
            hits += scan(route, body, COMMON)
        await ctx.close()
        for email, routes, rules in [
            ("owner@fictionalclient.example", CLIENT_ROUTES, COMMON + CLIENT_ONLY),
            ("ae@harbourline.example", AE_ROUTES, COMMON + AE_ONLY),
        ]:
            ctx = await browser.new_context(viewport={"width": 390, "height": 844} if "/c" in routes[0] else {"width": 1440, "height": 900})
            page = await ctx.new_page()
            r = await page.request.post(f"{base}/api/auth/dev/impersonate", data={"email": email})
            assert r.ok, f"impersonate {email}: {r.status}"
            if email.startswith("ae@"):
                q = await (await page.request.get(f"{base}/api/o/queue")).json()
                routes = routes + [f"/o/queue/{c['envelope_id']}" for c in (q.get("cards") or [])[:3]]
            await page.goto(f"{base}{routes[0]}", wait_until="load")  # warm the bundle
            await page.wait_for_timeout(4000)
            for route in routes:
                await page.goto(f"{base}{route}", wait_until="load")
                body = ""
                for _ in range(40):
                    await page.wait_for_timeout(500)
                    body = await page.inner_text("body")
                    if len(body) > 200 and "loading" not in body.lower()[:200]:
                        break
                if len(body) < 50:
                    hits.append(f"DENY empty render: {route} rendered no text")
                print(f"  scanned {route} as {email.split('@')[0]}: {len(body)} chars", file=sys.stderr)
                hits += scan(route, body, rules)
            await ctx.close()
        await browser.close()
    return hits


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("REACT_APP_BACKEND_URL")
                    or dotenv_values(ROOT / "frontend/.env").get("REACT_APP_BACKEND_URL"))
    hits = asyncio.run(sweep(ap.parse_args().base))
    for h in hits:
        print(h)
    print(f"deny-list (rendered DOM): {'FAIL ' + str(len(hits)) + ' violation(s)' if hits else 'OK'}")
    sys.exit(1 if hits else 0)


if __name__ == "__main__":
    main()
