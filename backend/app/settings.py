"""Runtime settings for the brokAId API (R5c-4a — Mongo-only)."""
from __future__ import annotations

import os

from pydantic import BaseModel

from app.kernel import devmode


def cors_origins() -> list[str]:
    """CORS is pinned to the app origin(s): APP_BASE_URL plus CORS_ORIGINS. A '*' is never honoured (credentials are on)."""
    from app.middleware.security import allowed_origins
    return sorted(allowed_origins())


def _assert_safe_to_start() -> None:
    """Fail closed: dev login needs an explicit dev ENVIRONMENT; production never boots with a CORS wildcard."""
    if (os.environ.get("AUTH_DEV_MODE") or "").strip().lower() == "true" and devmode.is_production():
        raise RuntimeError(
            "AUTH_DEV_MODE=true is forbidden unless ENVIRONMENT is development, preview or test "
            f"(ENVIRONMENT={os.environ.get('ENVIRONMENT')!r} is treated as production)",
        )
    raw = [o.strip() for o in (os.environ.get("CORS_ORIGINS") or "").split(",")]
    if devmode.is_production() and "*" in raw:
        raise RuntimeError("CORS_ORIGINS='*' with credentials is forbidden in production; list the app origin(s).")


class Settings(BaseModel):
    cors_origins: list[str]
    auth_dev_mode: bool
    environment: str
    resend_api_key: str | None = None
    emergent_llm_key: str | None = None
    sprint_cap_tokens: int | None = None
    sprint_cap_tier_thresholds: list[int] = [50, 80, 100]

    @classmethod
    def from_env(cls) -> "Settings":
        cap_raw = os.environ.get("SPRINT_CAP_TOKENS", "").strip()
        cap: int | None
        if not cap_raw or cap_raw.lower() in ("none", "null", "waived", "off"):
            cap = None
        else:
            cap = int(cap_raw)
        return cls(
            cors_origins=cors_origins(),
            auth_dev_mode=devmode.dev_mode(),
            environment=devmode.environment(),
            resend_api_key=(os.environ.get("RESEND_API_KEY") or None),
            emergent_llm_key=(os.environ.get("EMERGENT_LLM_KEY") or None),
            sprint_cap_tokens=cap,
        )

    def assert_safe_to_start(self) -> None:
        _assert_safe_to_start()


def get_settings() -> Settings:
    return Settings.from_env()
