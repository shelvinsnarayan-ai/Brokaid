"""Runtime settings for the brokAId API (R5c-4a — Mongo-only)."""
from __future__ import annotations

import os

from pydantic import BaseModel


def _assert_safe_to_start() -> None:
    """Refuse to boot if AUTH_DEV_MODE=true while ENVIRONMENT=production."""
    if (
        os.environ.get("AUTH_DEV_MODE", "false").lower() == "true"
        and os.environ.get("ENVIRONMENT", "development").lower()
        == "production"
    ):
        raise RuntimeError(
            "AUTH_DEV_MODE=true is forbidden in ENVIRONMENT=production",
        )


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
            cors_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
            auth_dev_mode=os.environ.get("AUTH_DEV_MODE", "false").lower() == "true",
            environment=os.environ.get("ENVIRONMENT", "development"),
            resend_api_key=(os.environ.get("RESEND_API_KEY") or None),
            emergent_llm_key=(os.environ.get("EMERGENT_LLM_KEY") or None),
            sprint_cap_tokens=cap,
        )

    def assert_safe_to_start(self) -> None:
        _assert_safe_to_start()


def get_settings() -> Settings:
    return Settings.from_env()
