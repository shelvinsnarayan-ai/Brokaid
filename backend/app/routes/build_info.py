"""GET /api/build-info — R1 #1 (Build Brief v2.1 §8).

Reports the deployed SHA, branch, and configuration presence flags so
production can be verified to match the pushed tip. Resolved once at
module import from the local git tree; if git is unavailable (source-
only checkout, container without .git) we fall back to environment
variables that a CI pipeline may inject at build time.

Never leaks the LLM key — only a boolean presence flag.
"""
from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter


router = APIRouter()


def _git(args: list[str], repo: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
        return out.stdout.strip() or None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _resolve_identity() -> dict[str, Any]:
    repo = Path(__file__).resolve().parents[3]

    sha = (
        os.environ.get("BROKAID_BUILD_SHA")
        or _git(["rev-parse", "HEAD"], repo)
        or None
    )
    short_sha = sha[:7] if sha else None
    branch = (
        os.environ.get("BROKAID_BUILD_BRANCH")
        or _git(["rev-parse", "--abbrev-ref", "HEAD"], repo)
        or None
    )
    committed_at = _git(["log", "-1", "--pretty=%cI"], repo)
    built_at = (
        os.environ.get("BROKAID_BUILT_AT")
        or committed_at
        or datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    env = os.environ.get("ENVIRONMENT", "development")
    return {
        "sha": sha,
        "short_sha": short_sha,
        "branch": branch,
        "built_at": built_at,
        "env": env,
    }


_IDENTITY: dict[str, Any] = _resolve_identity()


@router.get("/api/build-info")
async def build_info() -> dict[str, Any]:
    return {
        **_IDENTITY,
        "llm_key_present": bool(os.environ.get("EMERGENT_LLM_KEY")),
    }
