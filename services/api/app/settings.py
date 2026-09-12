"""Runtime settings for the brokAId API.

Env-only configuration. Phase-4 introduces ENVIRONMENT + auth guards:
`AUTH_DEV_MODE=true` in a production environment refuses to start.
"""
from __future__ import annotations

import os
from pydantic import BaseModel


class Settings(BaseModel):
    database_url: str
    cors_origins: list[str]
    auth_dev_mode: bool
    environment: str
    resend_api_key: str | None = None
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
            database_url=os.environ["DATABASE_URL"],
            cors_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
            auth_dev_mode=os.environ.get("AUTH_DEV_MODE", "false").lower() == "true",
            environment=os.environ.get("ENVIRONMENT", "development"),
            resend_api_key=(os.environ.get("RESEND_API_KEY") or None),
            sprint_cap_tokens=cap,
        )

    def assert_safe_to_start(self) -> None:
        """Startup guard: dev-mode auth in production is a hard error."""
        if self.auth_dev_mode and self.environment == "production":
            raise RuntimeError(
                "AUTH_DEV_MODE=true is not permitted when "
                "ENVIRONMENT=production. Refusing to start."
            )


def get_settings() -> Settings:
    return Settings.from_env()
