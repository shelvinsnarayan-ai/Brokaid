"""GET /api/build-info — R1 #1 (Build Brief v2.1 §8), R0.1 staleness fix.

SHA resolution:
  * Dev/preview pod (a `.git` dir exists): live `git rev-parse HEAD` per
    request, and a WARN at import if the baked `.build_sha` disagrees.
  * Production image (no `.git`): `BROKAID_BUILD_SHA` env, else the
    baked `/app/backend/.build_sha`. No git call.

Never leaks the LLM key — only a boolean presence flag.
"""
from __future__ import annotations

import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter

router = APIRouter()
log = logging.getLogger("brokaid.build_info")

_REPO = Path(__file__).resolve().parents[3]
_SHA_FILE = Path(__file__).resolve().parents[2] / ".build_sha"
_ENV_KEYS = ("ENVIRONMENT", "EMERGENT_ENV", "NODE_ENV")


def _git(args: list[str]) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=str(_REPO), check=True,
            capture_output=True, text=True, timeout=2,
        )
        return out.stdout.strip() or None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _read_build_sha_file() -> str | None:
    try:
        return _SHA_FILE.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _has_git_tree() -> bool:
    return (_REPO / ".git").exists()


def resolve_env() -> str:
    """'production' if any of ENVIRONMENT / EMERGENT_ENV / NODE_ENV says so."""
    for key in _ENV_KEYS:
        if (os.environ.get(key) or "").strip().lower() in ("production", "prod"):
            return "production"
    return "development"


def _resolve_identity() -> dict[str, Any]:
    if _has_git_tree():
        sha = _git(["rev-parse", "HEAD"]) or _read_build_sha_file()
        branch = _git(["rev-parse", "--abbrev-ref", "HEAD"])
        built_at = _git(["log", "-1", "--pretty=%cI"])
        source = "git"
    else:
        sha = os.environ.get("BROKAID_BUILD_SHA") or _read_build_sha_file()
        branch = os.environ.get("BROKAID_BUILD_BRANCH")
        built_at = os.environ.get("BROKAID_BUILT_AT")
        source = "baked"
    return {
        "sha": sha,
        "short_sha": sha[:7] if sha else None,
        "branch": branch,
        "built_at": built_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "env": resolve_env(),
        "sha_source": source,
    }


def warn_if_baked_sha_stale() -> None:
    if not _has_git_tree():
        return
    head, baked = _git(["rev-parse", "HEAD"]), _read_build_sha_file()
    if head and baked and head != baked:
        log.warning(
            "build-info: baked .build_sha=%s does not match git HEAD=%s — "
            "run scripts/bake_build_sha.sh before packaging", baked[:7], head[:7],
        )


warn_if_baked_sha_stale()
_BAKED_IDENTITY: dict[str, Any] | None = None if _has_git_tree() else _resolve_identity()


from app.kernel import email_channel  # noqa: E402


@router.get("/api/build-info")
async def build_info() -> dict[str, Any]:
    identity = _BAKED_IDENTITY or _resolve_identity()
    return {
        **identity,
        "auth_dev_mode": (os.environ.get("AUTH_DEV_MODE") or "").lower() == "true",
        "llm_key_present": bool(os.environ.get("EMERGENT_LLM_KEY")),
        "email_enabled": email_channel.email_enabled(),
    }
