"""R3.13 evidence: Operator can open Details; AE sees none; screenshots of AE queue, client timeline, claim detail, Governance at 390/1280."""
import asyncio
import json
import sys

from playwright.async_api import async_playwright

B = sys.argv[1].rstrip("/")
CLIENT = "/o/clients/00000000-0000-4000-8000-000000000001"


async def ready(p, url):
    for _ in range(3):
        try:
            await p.goto(B + url)
            await p.wait_for_selector('[data-testid="operator-main"] *', timeout=45000)
            await p.wait_for_timeout(3000)
            return
        except Exception:  # noqa: BLE001
            await p.wait_for_timeout(8000)


async def main():
    out = {}
    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        for who in ("ae", "operator"):
            c = await b.new_context()
            await c.request.post(f"{B}/api/auth/dev/impersonate", data={"email": f"{who}@harbourline.example"})
            p = await c.new_page()
            for w in (390, 1280):
                await p.set_viewport_size({"width": w, "height": 844 if w == 390 else 800})
                await ready(p, CLIENT)
                out[f"{who}_{w}_details_on_client"] = await p.locator('[data-testid^="client360-timeline-details-"]').count()
                if who == "operator" and out[f"{who}_{w}_details_on_client"]:
                    await p.locator('[data-testid="client360-timeline-details-0"] summary').click()
                    out[f"operator_{w}_details_opened"] = await p.locator('[data-testid="client360-timeline-details-0"] pre').is_visible()
                await p.locator('[data-testid="client360-timeline"]').scroll_into_view_if_needed()
                await p.screenshot(path=f"/tmp/r313/shot_{who}_client_timeline_{w}.png")
                if who == "ae":
                    await ready(p, "/o/queue")
                    await p.screenshot(path=f"/tmp/r313/shot_ae_queue_{w}.png")
                    await p.locator('[data-testid="queue-open-0"]').click()
                    await p.wait_for_timeout(4000)
                    txt = await p.evaluate("() => document.querySelector('[data-testid=operator-main]')?.innerText || ''")
                    import re
                    out[f"ae_{w}_queue_detail_leaks"] = re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{40,64}|\{\"", txt)[:3]
                    out[f"ae_{w}_playbook"] = await p.locator('[data-testid="playbook-endorsement"]').count()
                    await p.screenshot(path=f"/tmp/r313/shot_ae_queue_detail_{w}.png")
                    await ready(p, "/o/claims")
                    await p.locator('a[href^="/o/claims/"]').first.click()
                    await p.wait_for_timeout(4000)
                    out[f"ae_{w}_claim_playbook"] = await p.locator('[data-testid="playbook-claims"]').count()
                    await p.screenshot(path=f"/tmp/r313/shot_ae_claim_detail_{w}.png")
                    await ready(p, "/o/queue")
                    await p.locator('[data-testid="queue-legend-class"]').click()
                    out[f"ae_{w}_tooltip"] = await p.locator('[data-testid="queue-legend-class-text"]').is_visible()
                else:
                    await ready(p, "/o/governance")
                    out[f"operator_{w}_status_cards"] = await p.locator('[data-testid^="gov-status-"]').count()
                    await p.screenshot(path=f"/tmp/r313/shot_operator_governance_{w}.png")
                    if w == 390:
                        await p.click('[data-testid="operator-menu-open"]')
                        await p.wait_for_timeout(600)
                        await p.screenshot(path="/tmp/r313/shot_operator_menu_390.png")
            await c.close()
        await b.close()
    print(json.dumps(out, indent=1))


asyncio.run(main())
