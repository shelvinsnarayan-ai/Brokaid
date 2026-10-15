"""Kill-switch drill (R0.5): engage → measure time to effect → release → measure.

Usage: python scripts/kill_switch_drill.py [--agent intake] [--email principal@harbourline.example]
API base from $REACT_APP_BACKEND_URL or /app/frontend/.env. Uses dev impersonation.
"""
import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import dotenv_values

BASE = os.environ.get("REACT_APP_BACKEND_URL") or dotenv_values(
    Path(__file__).resolve().parents[1] / "frontend/.env").get("REACT_APP_BACKEND_URL")


def wait_for(c: httpx.Client, agent: str, blocked: bool, limit: float = 60.0) -> float:
    engaged_at = datetime.now(timezone.utc).isoformat()
    t0 = time.monotonic()
    while time.monotonic() - t0 < limit:
        if c.get("/api/o/kill-switch/check", params={"agent": agent}).json()["blocked"] is blocked:
            return time.monotonic() - t0
        time.sleep(0.25)
    raise SystemExit(f"FAIL: state blocked={blocked} not reached within {limit}s")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default="intake")
    ap.add_argument("--email", default="operator@harbourline.example")
    a = ap.parse_args()
    if not BASE:
        sys.exit("REACT_APP_BACKEND_URL not set")
    c = httpx.Client(base_url=BASE, timeout=30)
    c.post("/api/auth/dev/impersonate", json={"email": a.email}).raise_for_status()
    engaged_at = datetime.now(timezone.utc).isoformat()
    t0 = time.monotonic()
    sw = c.post("/api/o/kill-switch/engage", json={
        "scope_kind": "agent", "scope_ref": a.agent, "reason": "Pre-demo kill-switch drill"}).json()["switch"]
    wait_for(c, a.agent, True)
    on = time.monotonic() - t0  # includes engage round-trip
    print(f"ENGAGED {sw['switch_id']} on {a.agent}: effective in {on:.2f}s (target <60s)")
    t1 = time.monotonic()
    c.post("/api/o/kill-switch/release", json={"switch_id": sw["switch_id"], "reason": "Drill complete"}).raise_for_status()
    wait_for(c, a.agent, False)
    off = time.monotonic() - t1
    print(f"RELEASED: cleared in {off:.2f}s")
    result = "pass" if on < 60 and off < 60 else "fail"
    rec = c.post("/api/o/governance/drills", json={
        "agent": a.agent, "engage_s": round(on, 2), "release_s": round(off, 2), "result": result,
        "engaged_at": engaged_at, "released_at": datetime.now(timezone.utc).isoformat()})
    rec.raise_for_status()
    print(f"DrillRecorded {rec.json()['drill']['drill_id']}")
    print("DRILL PASS" if result == "pass" else "DRILL FAIL")


if __name__ == "__main__":
    main()
