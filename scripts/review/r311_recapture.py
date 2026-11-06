"""R3.11: retake blank evidence shots (dev server rendered nothing in time). Reads /tmp/r311/manifest.json; same ids and files."""
import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

BASE, OUT = sys.argv[1].rstrip("/"), Path("/tmp/r311")
PERSONA = {"C-": "owner@fictionalclient.example", "B-AE": "ae@harbourline.example", "B-CL": "ae@harbourline.example", "B-RE": "ae@harbourline.example",
           "B-AL": "ae@harbourline.example", "B-PR": "principal@harbourline.example", "B-OP": "operator@harbourline.example"}


def persona(sid: str):
    if sid.startswith("C-LOGIN"):
        return None
    return next((v for k, v in PERSONA.items() if sid.startswith(k)), None)


async def main():
    man = json.loads((OUT / "manifest.json").read_text())
    blank = [s for s in man["screens"] if (OUT / "shots" / s["file"]).stat().st_size < 9000 and not s["id"].startswith("T-")]
    print("blank:", [s["id"] for s in blank])
    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        ctxs = {}
        for s in blank:
            who = persona(s["id"])
            if who not in ctxs:
                c = await b.new_context(device_scale_factor=1)
                if who:
                    for _ in range(6):
                        r = await c.request.post(f"{BASE}/api/auth/dev/impersonate", data={"email": who})
                        if r.status != 429:
                            break
                        await asyncio.sleep(20)
                ctxs[who] = c
            p = await ctxs[who].new_page()
            w = 390 if s["id"].endswith("-390") else 1280
            await p.set_viewport_size({"width": w, "height": 844 if w == 390 else 800})
            await p.goto(f"{BASE}{s['url']}")
            try:
                await p.wait_for_function("(document.querySelector('#root')?.innerText || '').trim().length > 30", timeout=60000)
            except Exception:  # noqa: BLE001
                pass
            await p.wait_for_timeout(3000)
            await p.screenshot(path=str(OUT / "shots" / s["file"]), full_page=True)
            print(s["id"], (OUT / "shots" / s["file"]).stat().st_size)
            await p.close()
        await b.close()


asyncio.run(main())
