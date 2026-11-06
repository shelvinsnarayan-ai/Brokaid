"""R3.14 evidence capture (same as R3.11 plus the R3.12–R3.13c screens) on preview (Harbourline demo, read-only navigation). Screens at 390 and 1280, plus the 7 client jobs (A20 §3)
measured at 390: clicks, fields and screens to success. Never presses a final submit. Writes /tmp/r314/manifest.json and shots/*.png.
Usage: python scripts/review/r311_capture.py https://<preview-host>"""
import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

BASE = sys.argv[1].rstrip("/")
OUT = Path("/tmp/r314")
RENEW_POLICY = "00000000-0000-4000-8000-000000000151"
CID = "00000000-0000-4000-8000-000000000001"
SHOTS = OUT / "shots"
CLM13 = "a3cbd499-6a45-4ca4-9b17-f8d62b45ef40"
CLM23 = "65e0039b-ac3d-4e39-bc06-dc91f04199cc"
SIZES = {"390": {"width": 390, "height": 844}, "1280": {"width": 1280, "height": 800}}


_STATE: dict = {}


async def ctx_for(browser, email, size):
    """One impersonation per persona (auth is rate limited); later contexts reuse the cookies."""
    if email in _STATE:
        return await browser.new_context(viewport=SIZES[size], device_scale_factor=1, storage_state=_STATE[email])
    c = await browser.new_context(viewport=SIZES[size], device_scale_factor=1)
    if email:
        for _ in range(6):
            r = await c.request.post(f"{BASE}/api/auth/dev/impersonate", data={"email": email})
            if r.status != 429:
                break
            await asyncio.sleep(20)
        assert r.ok, (email, r.status)
        if BASE.startswith("http://localhost"):  # __Host- cookie: add it ourselves (localhost counts as a secure context)
            for h in r.headers_array:
                if h["name"].lower() == "set-cookie":
                    name, val = h["value"].split(";", 1)[0].split("=", 1)
                    await c.add_cookies([{"name": name, "value": val, "domain": "localhost", "path": "/", "secure": True, "httpOnly": True}])
        _STATE[email] = await c.storage_state()
    return c


async def shot(page, sid, caption, items, full=True):
    try:  # dev (Vite) server: wait for the app to actually render
        await page.wait_for_function("(document.querySelector('#root')?.innerText || '').trim().length > 30", timeout=25000)
    except Exception:  # noqa: BLE001
        pass
    await page.wait_for_timeout(1500)
    if "security verification" in (await page.content()):
        raise SystemExit(f"{sid}: Cloudflare challenge, not the app")
    p = SHOTS / f"{sid}.png"
    await page.screenshot(path=str(p), full_page=full)
    items.append({"id": sid, "caption": caption, "url": page.url.replace(BASE, ""), "file": p.name})


async def ids(c):
    g = lambda p: c.request.get(f"{BASE}{p}")  # noqa: E731
    r0 = await g("/api/c/policies")
    if not r0.ok or "json" not in r0.headers.get("content-type", ""):
        raise SystemExit(f"policies: {r0.status} {(await r0.text())[:200]}")
    pol = (await r0.json())["policies"]
    act = (await (await g("/api/c/activity")).json())["items"]
    ren = (await (await g("/api/c/renewals")).json())["renewals"]
    pay = next((i["href"] for i in act if "/pay/" in (i.get("href") or "")), None)
    return {"policy": pol[0]["policy_id"], "client": pol[0]["client_id"], "pay": pay, "renewal": ren[0]["renewal_id"] if ren else None}


def client_screens(x):
    s = [("HOME", "/c", "Client Home"), ("POLICY", f"/c/policy/{x['policy']}", "Policy detail"), ("ACTIVITY", "/c/activity", "Activity incl. payments due"),
         ("CHANGE", f"/c/cover/{x['policy']}/request-change", "Change cover request (not submitted)"),
         ("CERT", f"/c/cover/{x['policy']}/certificate/new", "Certificate request (not submitted)"), ("CLAIM-NEW", "/c/claims/new", "Claim lodge (not submitted)"),
         ("TRACKER", f"/c/claims/{CLM13}", "Claim tracker CLM-2026-000013 with repair quote card"), ("MESSAGES", "/c/messages", "Messages list"),
         ("THREAD", f"/c/messages/claim/{CLM13}", "Claim thread with photos and broker PDF quote"), ("PROFILE", "/c/profile", "Profile"),
         ("DOCUMENTS", "/c/documents", "Documents"), ("NOTIFS", "/c/notifications", "Notification settings"), ("SECURITY", "/account/security", "Security")]
    if x["pay"]:
        s.insert(3, ("PAY", x["pay"], "Pay screen (not paid)"))
    if x["renewal"]:
        s.append(("RENEWAL", f"/c/renewal/{x['renewal']}", "Renewal (accept not pressed)"))
    s.append(("POLICY-RENEWAL", f"/c/policy/{RENEW_POLICY}", "Business pack policy with the Renewal section (R3.12)"))
    return s


# R3.14: interactions captured after opening something (never submitting). (id, route, click selector, caption)
CLIENT_OPEN = [("PEOPLE-INVITE", "/c/profile", '[data-testid="people-invite-open"]', "Profile: People on this account with the invite form open (not sent)"),
               ("EXPLAIN", f"/c/policy/{RENEW_POLICY}", '[data-testid^="explain-"]:not([data-testid$="-panel"])', "Policy explainer opened ('?')")]
STAFF_OPEN = [("ae@harbourline.example", "B-AE-MENU", "/o/queue", '[data-testid="operator-menu-open"]', "Phone menu open (390 only)", ("390",)),
              ("ae@harbourline.example", "B-AE-SCHEDULES", f"/o/clients/{CID}", '[data-testid="pdoc-section"]',
               "Client view: Schedules & PDS section with upload (scrolled into view)", ("390", "1280")),
              ("principal@harbourline.example", "B-PR-ADVICE", f"/o/inbox/claim-{CLM23}", '[data-testid="advice-approvals"]',
               "Principal: pending advice approval with the exact file (scrolled into view; TEST_ fixture)", ("390", "1280"))]


BROKER = [("ae@harbourline.example", [("B-AE-INBOX", "/o/inbox", "AE inbox"), ("B-AE-QUEUE", "/o/queue", "AE queue / Today"),
                                      ("B-CLIENT", "/o/clients/00000000-0000-4000-8000-000000000001", "Client view (Harbour Marine Services)"),
                                      ("B-RENEWALS", "/o/renewals", "Renewal pipeline"), ("B-RENEWAL-REVIEW", "/o/renewal-review", "Renewal review"),
                                      ("B-CLAIMS", "/o/claims", "Claims list"), ("B-CLAIM", f"/o/claims/{CLM13}", "Claim detail with Documents"),
                                      ("B-CLAIM-MSG", f"/o/inbox/claim-{CLM13}", "Claim Messages panel (staff thread)"),
                                      ("B-ALERTS", "/o/alerts", "Alerts + push test card"),
                                      ("B-AE-USERS", "/o/users", "Users list (stacked table at 390)")]),
          ("principal@harbourline.example", [("B-PR-APPROVAL", f"/o/inbox/claim-{CLM23}", "Principal view of a claim thread with a pending advice approval with files (TEST_ fixture)"),
                                             ("B-PR-QUEUE", "/o/queue", "Principal queue (endorsement approvals)")]),
          ("operator@harbourline.example", [("B-OP-GOV", "/o/governance", "Operator Governance incl. Release Check and Exposure report")])]

# A20 §3 jobs. Each step: candidate selectors (first visible wins). Final "goal" = selector that proves the job's screen is reached.
JOBS = [
    ("J1", "Is the ute / unit / shed insured, for how much?", [['a[href^="/c/policy/"]', '[data-testid^="mer-vehicle-"]', 'text="Isuzu D-Max · DEF-456"']],
     '[data-testid="mer-policy-figure"], [data-testid="mer-policy-covered"]'),
    ("J2", "Get a certificate naming a counterparty", [['[data-testid="mer-plus"]'], ['text="Get a certificate"'], ['text="Marine Liability"']],
     'input, textarea'),
    ("J3", "Lodge a claim for a specific thing", [['[data-testid="mer-plus"]'], ['text="Make a claim"']], 'form, [data-testid*="claim-new"], [data-testid*="lodge"]'),
    ("J4", "See what I owe and how to pay", [['text="Pay now"']], 'text=/Pay|BSB|card/i'),
    ("J5", "Confirm renewal details and sums insured", [['[data-testid="mer-attention-renewal_due"]', 'text="Isuzu D-Max · DEF-456"']],
     'text=/accept|confirm|still right|Send my details/i'),  # R3.12 checklist: "Are these sums insured … still right?"
    ("J6", "Let my spouse / bookkeeper act on this", [['[data-testid="mer-tab-profile"]'], ['text=/Add a person|Invite|Who can act|People/']],
     'text=/invite|bookkeeper|add a person/i'),
    ("J7", "Find last year's schedule / PDS", [['[data-testid="mer-tab-profile"]'], ['text="Documents"'], ['text=/Schedule|PDS/']], 'text=/Schedule|PDS/'),
]


async def run_job(c, jid, title, steps, goal):
    page = await c.new_page()
    await page.goto(f"{BASE}/c")
    try:
        await page.wait_for_function("(document.querySelector('#root')?.innerText || '').trim().length > 30", timeout=60000)
    except Exception:  # noqa: BLE001
        pass
    seq, clicks, screens, note = [], 0, 1, ""
    await shot(page, f"T-{jid}-0", f"{title}: start (Home)", seq)
    for i, cands in enumerate(steps, 1):
        hit = None
        for sel in cands:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible():
                hit = loc
                break
        if not hit:
            note = f"blocked at step {i}: none of {cands} visible on {page.url.replace(BASE, '')}"
            break
        before = page.url
        await hit.click()
        clicks += 1
        await page.wait_for_timeout(1200)
        screens += 1 if page.url != before else 0
        await shot(page, f"T-{jid}-{i}", f"{title}: after tap {clicks}", seq)
    reached = await page.locator(goal).first.count() > 0 and not note
    await page.close()
    return {"job": jid, "title": title, "taps": clicks, "screens": screens, "reached_goal_screen": bool(reached), "note": note or "stopped before any submit",
            "sequence": [s["id"] for s in seq]}, seq


def save(items, tasks):
    (OUT / "manifest.json").write_text(json.dumps({"screens": items, "tasks": tasks}, indent=1))


async def main():
    SHOTS.mkdir(parents=True, exist_ok=True)
    items, tasks = [], []
    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        c = await ctx_for(b, "owner@fictionalclient.example", "390")
        x = await ids(c)
        for size in SIZES:
            for key, path, cap in client_screens(x):
                if (SHOTS / f"C-{key}-{size}.png").exists() and (SHOTS / f"C-{key}-{size}.png").stat().st_size > 9000:
                    items.append({"id": f"C-{key}-{size}", "caption": cap, "url": path, "file": f"C-{key}-{size}.png"})
                    if key == "HOME" and (SHOTS / f"C-PLUS-{size}.png").exists():
                        items.append({"id": f"C-PLUS-{size}", "caption": "The + action sheet", "url": path, "file": f"C-PLUS-{size}.png"})
                    continue
                p = await c.new_page()
                await p.set_viewport_size(SIZES[size])
                await p.goto(f"{BASE}{path}")
                await shot(p, f"C-{key}-{size}", cap, items)
                if key == "HOME":
                    plus = p.locator('[data-testid="mer-plus"]')
                    if await plus.count():
                        await plus.click()
                        await shot(p, f"C-PLUS-{size}", "The + action sheet", items)
                await p.close()
        for size in SIZES:
            for key, path, sel, cap in CLIENT_OPEN:
                p = await c.new_page()
                await p.set_viewport_size(SIZES[size])
                await p.goto(f"{BASE}{path}")
                await p.wait_for_timeout(2500)
                loc = p.locator(sel).first
                if await loc.count():
                    await loc.scroll_into_view_if_needed()
                    await loc.click()
                    await p.wait_for_timeout(800)
                await shot(p, f"C-{key}-{size}", cap, items, full=False)
                await p.close()
        for j in JOBS:
            p0 = await c.new_page()
            await p0.close()
            t, seq = await run_job(c, *j)
            tasks.append(t)
            items += seq
            save(items, tasks)
        await c.close()
        lc = await ctx_for(b, None, "390")
        for size in SIZES:
            p = await lc.new_page()
            await p.set_viewport_size(SIZES[size])
            await p.goto(f"{BASE}/login")
            await shot(p, f"C-LOGIN-{size}", "Login (preview shows demo panel)", items)
            await p.close()
        await lc.close()
        for email, screens in BROKER:
            await asyncio.sleep(3)
            c = await ctx_for(b, email, "1280")
            for size in SIZES:
                for sid, path, cap in screens:
                    p = await c.new_page()
                    await p.set_viewport_size(SIZES[size])
                    await p.goto(f"{BASE}{path}")
                    await shot(p, f"{sid}-{size}", cap, items)
                    await p.close()
            await c.close()
        for email, sid, path, sel, cap, sizes in STAFF_OPEN:
            c = await ctx_for(b, email, "1280")
            for size in sizes:
                p = await c.new_page()
                await p.set_viewport_size(SIZES[size])
                await p.goto(f"{BASE}{path}")
                await p.wait_for_timeout(3000)
                loc = p.locator(sel).first
                if await loc.count():
                    await loc.scroll_into_view_if_needed()
                    if sid == "B-AE-MENU":
                        await loc.click()
                    await p.wait_for_timeout(800)
                await shot(p, f"{sid}-{size}", cap, items, full=False)
                await p.close()
            await c.close()
        await b.close()
    save(items, tasks)
    print(len(items), "shots;", json.dumps(tasks, indent=0)[:3000])


asyncio.run(main())
