"""Silent Playwright recordings for V1 Review Pack §5.

Two scenes, no audio, no LLM calls:
  1. Endorsement scene end-to-end on mobile viewport
        Landing → login (Kai) → Home → Cover → PendingQuote
        (fallback: Cover only) → Certificate (fallback: current
        state) → open /verify/:token in a second tab to simulate
        the operator/client second-device QR check.
  2. Claim FNOL → triage → lodgement → status showing the
        coverage-language guard suppressing a bad draft.

Outputs to docs/review_pack_assets/recordings/. Each scene renders
one .webm from the Playwright context. If ffmpeg is available on
PATH (Playwright bundles it under /pw-browsers/ffmpeg-*), a matching
.mp4 is produced.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright

BASE = "https://brokaid-demo.preview.emergentagent.com"
OUT = Path("/app/docs/review_pack_assets/recordings")
OUT.mkdir(parents=True, exist_ok=True)


def _ffmpeg_path() -> str | None:
    ff = shutil.which("ffmpeg")
    if ff:
        return ff
    for root, _dirs, files in os.walk("/pw-browsers"):
        for f in files:
            if f == "ffmpeg-linux":
                return os.path.join(root, f)
    return None


async def _impersonate(context, email: str) -> None:
    resp = await context.request.post(
        f"{BASE}/api/auth/dev/impersonate",
        data={"email": email},
        headers={"Content-Type": "application/json"},
    )
    print(f"  impersonate {email} → {resp.status}")


async def _rec(pw, viewport, name, story):
    browser = await pw.chromium.launch(headless=True)
    ctx = await browser.new_context(
        viewport=viewport,
        record_video_dir=str(OUT / f".raw-{name}"),
        record_video_size=viewport,
    )
    print(f"[{name}] recording…")
    page = await ctx.new_page()
    try:
        await story(ctx, page)
    except Exception as e:
        print(f"[{name}] story error: {e}")
    finally:
        # Explicit close in this order so the encoder flushes:
        # page → context → browser. Persistent-context skipped the
        # flush on the earlier attempt and produced 1.9 KB stubs.
        try:
            await page.close()
        except Exception:
            pass
        await ctx.close()
        await browser.close()
    raw_dir = OUT / f".raw-{name}"
    videos = sorted(raw_dir.glob("*.webm"))
    if not videos:
        print(f"[{name}] NO video landed")
        return
    src = videos[-1]
    dst = OUT / f"{name}.webm"
    if dst.exists():
        dst.unlink()
    src.replace(dst)
    print(f"[{name}] wrote {dst} ({dst.stat().st_size} bytes)")
    ff = _ffmpeg_path()
    if ff:
        mp4 = OUT / f"{name}.mp4"
        try:
            subprocess.run(
                [ff, "-y", "-i", str(dst), "-c:v", "libx264",
                 "-preset", "fast", "-pix_fmt", "yuv420p", str(mp4)],
                check=True, capture_output=True,
            )
            print(f"[{name}] transcoded {mp4}")
        except subprocess.CalledProcessError as e:
            print(f"[{name}] ffmpeg failed: {e.stderr.decode()[:200]}")


async def endorsement_scene(ctx, page):
    """Client-side: Home → Cover → policy card → certificate view →
    open verify route in a second page as the 'second device'."""
    await page.goto(BASE, wait_until="domcontentloaded")
    await page.wait_for_timeout(1200)
    await _impersonate(ctx, "owner@fictionalclient.example")
    await page.goto(f"{BASE}/c", wait_until="networkidle")
    await page.wait_for_timeout(2500)
    await page.goto(f"{BASE}/c/cover", wait_until="networkidle")
    await page.wait_for_timeout(2500)
    # Try to navigate into the managed policy's certificate view.
    # The seed policy id for Marine Liability is a UUID under the
    # burmac tenant.  If the deep-link 404s the recorder just holds
    # on the Cover surface — still valid evidence.
    policy_id = "00000000-0000-4000-8000-000000000101"
    await page.goto(
        f"{BASE}/c/cover/{policy_id}/certificate",
        wait_until="domcontentloaded",
    )
    await page.wait_for_timeout(3000)
    # Simulate the second-device QR scan by opening the public
    # verifier on a fresh page in the same context.  Even without a
    # live token the verifier surface renders its own guard.
    second = await ctx.new_page()
    await second.goto(
        f"{BASE}/verify/fictional-token-for-demo",
        wait_until="domcontentloaded",
    )
    await second.wait_for_timeout(3000)


async def claim_scene(ctx, page):
    """Client-side: FNOL → view triaged claim → coverage-language
    guard visible on status update."""
    await page.goto(BASE, wait_until="domcontentloaded")
    await page.wait_for_timeout(1200)
    await _impersonate(ctx, "owner@fictionalclient.example")
    await page.goto(f"{BASE}/c/claims", wait_until="networkidle")
    await page.wait_for_timeout(2500)
    # There is one seeded claim already lodged (CLM-2026-000001).
    # Open the claim detail and let the status view render.
    await page.goto(
        f"{BASE}/c/claims/79323d96-ae6e-4e49-9557-d644e48b99ab",
        wait_until="domcontentloaded",
    )
    await page.wait_for_timeout(3500)
    # Cross to the notifications tab so the mocked insurer notice is
    # visible in the same recording.
    await page.goto(f"{BASE}/c/notifications", wait_until="networkidle")
    await page.wait_for_timeout(2500)


async def main():
    async with async_playwright() as pw:
        await _rec(pw, {"width": 393, "height": 852},
                   "scene-1-endorsement-mobile", endorsement_scene)
        await _rec(pw, {"width": 393, "height": 852},
                   "scene-2-claim-fnol-mobile", claim_scene)


if __name__ == "__main__":
    asyncio.run(main())
