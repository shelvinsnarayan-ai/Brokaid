"""Preview-only demo sign-in panel: never mounted/served in production, never compiled into a production bundle."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


def _paths(app) -> set[str]:
    return set((app.openapi().get("paths") or {}))


def test_personas_route_absent_in_production(monkeypatch):
    from app.main import create_app
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("AUTH_DEV_MODE", "true")
    assert "/api/auth/dev/personas" in _paths(create_app(seed_dev_users=False))
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_DEV_MODE", "true")  # even if someone leaves the flag on
    monkeypatch.setenv("CORS_ORIGINS", "https://app.brokaid.example")
    with pytest.raises(RuntimeError):
        create_app(seed_dev_users=False)
    monkeypatch.setenv("AUTH_DEV_MODE", "false")
    paths = _paths(create_app(seed_dev_users=False))
    assert not any(p.startswith("/api/auth/dev") for p in paths)


@pytest.mark.skipif(not (FRONTEND / "node_modules" / ".bin" / "vite").exists() or not shutil.which("node"),
                    reason="frontend toolchain not installed")
def test_production_bundle_has_no_demo_panel(tmp_path):
    env = {**os.environ, "ENVIRONMENT": "production", "AUTH_DEV_MODE": "false", "NODE_ENV": "production"}
    out = tmp_path / "dist"
    subprocess.run([str(FRONTEND / "node_modules" / ".bin" / "vite"), "build", "--outDir", str(out), "--emptyOutDir"],
                   cwd=FRONTEND, env=env, check=True, capture_output=True, timeout=280)
    text = "".join(p.read_text(errors="ignore") for p in out.rglob("*.js"))
    assert text, "build produced no JS"
    for needle in ("Demo sign-in (preview only)", "demo-signin-panel", "/api/auth/dev/", "dev-login-root",
                   "login-personas", "brokaid:dev-only", "Prepare client renewal", "DEMO MODE ON A NON-PREVIEW HOST", "demo-signin-kai-reset", "kai-renewal",
                   "Reset sandbox", "sandbox-reset", "sandbox/reset", "This clears the made-up business details"):
        assert needle not in text, f"{needle!r} leaked into the production bundle"
