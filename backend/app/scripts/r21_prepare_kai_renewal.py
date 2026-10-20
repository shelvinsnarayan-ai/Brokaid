"""Demo action: "Prepare Kai renewal at accept step" — Harbourline (demo tenant) + dev mode only. Never accepts.

    cd /app/backend && python -m app.scripts.r21_prepare_kai_renewal [--api http://localhost:8001]
Calls the dev-only endpoint (404 outside dev mode / demo tenants). Re-runnable: earlier renewals on …0150 are reset (ledgered).
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8001")
    a = ap.parse_args()
    r = httpx.post(f"{a.api}/api/auth/dev/demo/kai-renewal", timeout=180)
    if r.status_code != 200:
        raise SystemExit(f"Refused or failed: {r.status_code} {r.text[:300]}")
    d, public = r.json(), os.environ.get("APP_BASE_URL", "").rstrip("/")
    print(f"renewal    {d['renewal_id']}  stage={d['stage']}  (reset earlier: {len(d['reset'])})")
    print(f"chosen     {d['insurer']}  premium A${d['premium']}  excess A${d['excess']}  CRN {d['crn']}")
    print(f"doc sha256 {d['doc_sha256']}")
    print(f"accept     {public}{d['accept_path']}")
