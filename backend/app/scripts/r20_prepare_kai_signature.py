"""Demo action: "Prepare Kai at signature stage" — Harbourline (demo tenant) only.

Drives the canonical Hilux→Ranger swap on Kai's motor policy through the live API:
request → (MOCKED) insurer quote → Cass approves → Kai approves → Kai advises paid,
so the envelope waits at awaiting_signature for a click-to-accept. It never accepts.

    cd /app/backend && python -m app.scripts.r20_prepare_kai_signature [--api http://localhost:8001]
Re-runnable: each run opens a new envelope (needs Hilux ABC-123 still on the policy).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import time
import uuid
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

KAI, CASS = "owner@fictionalclient.example", "ae@harbourline.example"
POLICY = "00000000-0000-4000-8000-000000000104"  # Kai's canonical motor policy (seeded)
SWAP = ("Please swap the Hilux ABC-123 on my motor policy for a new 2025 Ford Ranger XLT, "
        "rego XYZ-789, from next Monday.")


async def _guard() -> None:
    from motor.motor_asyncio import AsyncIOMotorClient

    from app.kernel import devmode
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    t = await db.tenants.find_one({"slug": "harbourline"})  # tenant-safe: system-only — resolve the demo tenant
    if not devmode.dev_mode() or not devmode.is_demo_row(t):
        raise SystemExit("Refusing: demo action runs only in dev mode on the Harbourline demo tenant.")
    pol = await db.policies.find_one({"tenant_id": t["tenant_id"], "policy_id": POLICY})  # tenant-safe: scoped by tenant_id
    if not pol or not any((v.get("rego") or "").replace("-", "") == "ABC123" for v in (pol.get("schedule") or {}).get("vehicles") or []):
        raise SystemExit("Kai's policy no longer has the Hilux ABC-123 (a previous run was accepted). Re-seed Harbourline first.")


async def _cookie(http, email: str) -> dict:
    r = await http.post("/api/auth/dev/impersonate", json={"email": email})
    r.raise_for_status()
    name, _, rest = r.headers["set-cookie"].partition("=")
    return {"Cookie": f"{name}={rest.split(';', 1)[0]}"}


def _ok(r: httpx.Response, what: str) -> httpx.Response:
    if r.status_code != 200:
        raise SystemExit(f"{what} failed: {r.status_code} {r.text[:200]}")
    return r


async def main(api: str, public: str) -> None:
    await _guard()
    async with httpx.AsyncClient(base_url=api, timeout=60) as http:
        kai, cass = await _cookie(http, KAI), await _cookie(http, CASS)
        base = f"/api/c/matters/{POLICY}"
        env = _ok(await http.post(f"{base}/request-change", headers=kai,
                                  json={"raw_message": SWAP, "idempotency_key": f"demo-sign:{uuid.uuid4()}"}), "Change request").json()["envelope_id"]
        print("envelope", env, "— waiting for the (MOCKED) insurer quote…")
        deadline, state = time.monotonic() + 180, None
        while time.monotonic() < deadline and state != "awaiting_operator":
            state = (await http.get(f"{base}/status", headers=kai)).json().get("state")
            if state == "awaiting_broker_review":
                raise SystemExit(f"Request {env} was parked for a person instead of quoted; re-run.")
            await asyncio.sleep(2)
        if state != "awaiting_operator":
            raise SystemExit(f"No quote within 180s (state {state}).")
        _ok(await http.post(f"/api/o/queue/{env}/approve", headers=cass, json={"reason": "Demo: prepare Kai at signature"}), "Cass approval")
        _ok(await http.post(f"{base}/approve/{env}", headers=kai), "Kai approval")
        _ok(await http.post(f"{base}/paid/{env}", headers=kai, json={}), "Payment advice")
        state = (await http.get(f"{base}/status", headers=kai)).json().get("state")
        acc = _ok(await http.get(f"/api/c/acceptance/endorsement/{env}", headers=kai), "Acceptance document").json()
    print(f"state      {state}")
    print(f"envelope   {env}")
    print(f"doc sha256 {acc['doc_sha256']}  ready={acc['ready']}  accepted={bool(acc['accepted'])}")
    print(f"sign page  {public}/c/cover/{POLICY}/sign/{env}")
    print(f"receipt    {public}/c/acceptance/endorsement/{env}  (populated only after Kai accepts)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8001")
    ap.add_argument("--public", default=os.environ.get("APP_BASE_URL", "").rstrip("/"))
    a = ap.parse_args()
    asyncio.run(main(a.api, a.public))
