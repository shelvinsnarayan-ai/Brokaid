"""R3.12 rerun of the R3.11 §3 task-path measurement only (Playwright, no models). Same jobs, same start (Home at 390), same rules:
first visible candidate wins, never presses a final submit. J5–J7 candidates now include the R3.12 entry points.
Usage: python scripts/review/r312_tasks.py https://<preview-host>"""
import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

BASE = sys.argv[1].rstrip("/")
OUT = Path("/tmp/r312")
JOBS = [
    ("J1", "Is the ute / unit / shed insured, for how much?", [['a[href^="/c/policy/"]', '[data-testid^="mer-vehicle-"]', 'text="Isuzu D-Max · DEF-456"']],
     '[data-testid="mer-policy-figure"], [data-testid="mer-policy-covered"]'),
    ("J2", "Get a certificate naming a counterparty", [['[data-testid="mer-plus"]'], ['text="Get a certificate"'], ['text="Marine Liability"']], 'input, textarea'),
    ("J3", "Lodge a claim for a specific thing", [['[data-testid="mer-plus"]'], ['text="Make a claim"']], 'form, [data-testid*="claim-new"], [data-testid*="lodge"]'),
    ("J4", "See what I owe and how to pay", [['text="Pay now"']], 'text=/Pay|BSB|card/i'),
    ("J5", "Confirm renewal details and sums insured", [['[data-testid="mer-attention-renewal_due"]', 'text=/Review renewal/']],
     '[data-testid="checklist-item-values"] [data-testid$="-change"]'),
    ("J6", "Let my spouse / bookkeeper act on this", [['[data-testid="mer-tab-profile"]', 'a[href="/c/profile"]'], ['[data-testid="people-invite-open"]']],
     '[data-testid="people-preset-bookkeeper"]'),
    ("J7", "Find last year's schedule / PDS", [['[data-testid="mer-tab-profile"]', 'a[href="/c/profile"]'], ['[data-testid="mer-profile-documents"]', 'text="Documents"']],
     '[data-testid$="-previous"] a[href*="/documents/file/"]'),
]


async def visible(page, cands):
    for sel in cands:
        loc = page.locator(sel).first
        if await loc.count() and await loc.is_visible():
            return loc
    return None


async def run_job(c, jid, title, steps, goal):
    page = await c.new_page()
    await page.goto(f"{BASE}/c")
    await page.wait_for_selector('[data-testid="mer-tab-home"]', timeout=90000)
    await page.wait_for_timeout(2500)
    clicks, screens, note = 0, 1, ""
    for i, cands in enumerate(steps, 1):
        hit = await visible(page, cands)
        if not hit:
            note = f"blocked at step {i}: none of {cands} visible on {page.url.replace(BASE, '')}"
            break
        before = page.url
        await hit.click()
        clicks += 1
        await page.wait_for_timeout(2500)
        screens += 1 if page.url != before else 0
    extra = {}
    if jid == "J7" and not note:
        extra["pds_visible"] = await page.locator('[data-testid$="-pds"] a').count() > 0
        extra["current_schedule_visible"] = await page.locator('[data-testid$="-schedule"] a').count() > 0
    reached = not note and await page.locator(goal).first.count() > 0
    await page.screenshot(path=str(OUT / f"T-{jid}-end.png"))
    await page.close()
    return {"job": jid, "title": title, "taps": clicks, "screens": screens, "reached_goal_screen": bool(reached),
            "note": note or "stopped before any submit", **extra}


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        c = await b.new_context(viewport={"width": 390, "height": 844})
        r = await c.request.post(f"{BASE}/api/auth/dev/impersonate", data={"email": "owner@fictionalclient.example"})
        assert r.ok, r.status
        tasks = [await run_job(c, *j) for j in JOBS]
        await b.close()
    (OUT / "tasks.json").write_text(json.dumps(tasks, indent=1))
    print(json.dumps(tasks, indent=1))


asyncio.run(main())
