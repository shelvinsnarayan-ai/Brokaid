"""R3.13 staff screen check (Playwright): as AE (and Principal), every staff route at 390 and 1280 must show no raw ids/hashes/JSON,
page width == viewport at 390 (no horizontal scroll) and primary actions not clipped.
Usage: python scripts/review/r313_staff_scan.py https://<preview-host> [persona-email]"""
import asyncio
import json
import re
import sys
from pathlib import Path

from playwright.async_api import async_playwright

BASE = sys.argv[1].rstrip("/")
WHO = sys.argv[2] if len(sys.argv) > 2 else "ae@harbourline.example"
OUT = Path("/tmp/r313")
ROUTES = ["/o/queue", "/o/inbox", "/o/claims", "/o/renewals", "/o/renewal-review", "/o/clients", "/o/alerts", "/o/governance",
          "/o/invites", "/o/users", "/o/policies", "/o/matters", "/o/compliance", "/o/clients/00000000-0000-4000-8000-000000000001"]
PAT = {"uuid": re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I),
       "sha": re.compile(r"\b[0-9a-f]{40,64}\b", re.I), "json": re.compile(r'\{"')}
DRILL = {"/o/clients": 'a[href^="/o/clients/"]', "/o/claims": 'a[href^="/o/claims/"]', "/o/inbox": 'a[href^="/o/inbox/"]',
         "/o/queue": 'a[href^="/o/queue/"]'}


async def check(page, route, w):
    await page.wait_for_timeout(3500)
    text = await page.evaluate("() => document.querySelector('[data-testid=operator-main]')?.innerText || ''")
    hits = {k: sorted(set(p.findall(text)))[:3] for k, p in PAT.items() if p.search(text)}
    sw, vw = await page.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
    clipped = await page.evaluate("""() => [...document.querySelectorAll('main button, main a.mer-btn, main .brokaid-btn, main [data-primary]')]
      .filter(b => { const r = b.getBoundingClientRect(); return r.width > 0 && (r.right > innerWidth + 1 || r.left < -1); })
      .slice(0, 3).map(b => (b.innerText || b.getAttribute('aria-label') || '').slice(0, 30))""")
    if w == 390 or not Path(OUT / f"{route.strip('/').replace('/', '_')}_{w}.png").exists():
        await page.screenshot(path=str(OUT / f"{route.strip('/').replace('/', '_')}_{w}.png"))
    return {"route": route, "w": w, "leaks": hits, "overflow": sw > vw + 1 if w == 390 else False, "clipped": clipped if w == 390 else []}


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    res = []
    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        c = await b.new_context()
        r = await c.request.post(f"{BASE}/api/auth/dev/impersonate", data={"email": WHO})
        assert r.ok, r.status
        page = await c.new_page()
        for w in (390, 1280):
            await page.set_viewport_size({"width": w, "height": 844 if w == 390 else 800})
            for route in ROUTES:
                try:
                    for attempt in range(3):
                        try:
                            await page.goto(BASE + route)
                            await page.wait_for_selector('[data-testid="operator-main"] *', timeout=45000)
                            break
                        except Exception:  # noqa: BLE001
                            if attempt == 2:
                                raise
                            await page.wait_for_timeout(8000)
                    res.append(await check(page, route, w))
                    if route in DRILL and await page.locator(DRILL[route]).count():
                        await page.locator(DRILL[route]).first.click()
                        res.append(await check(page, page.url.replace(BASE, "").split("?")[0], w))
                except Exception as e:  # noqa: BLE001
                    res.append({"route": route, "w": w, "error": str(e)[:120]})
        await b.close()
    bad = [r for r in res if r.get("leaks") or r.get("overflow") or r.get("clipped") or r.get("error")]
    (OUT / f"scan_{WHO.split('@')[0]}.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(bad, indent=1))
    print(f"{len(res)} checks, {len(bad)} with problems")


asyncio.run(main())
