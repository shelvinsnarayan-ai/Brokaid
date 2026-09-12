"""Runtime settings for the brokAId API.

All configuration comes from environment variables loaded via
python-dotenv in main.py. No defaults for URLs or credentials —
missing config should fail fast at startup.
"""
from __future__ import annotations

import os
from pydantic import BaseModel


class Settings(BaseModel):
    database_url: str
    cors_origins: list[str]
    auth_dev_mode: bool
    # Phase 3 — brAIn sprint budget.
    # None means the cap is waived; usage is still projected in
    # proj_sprint_usage so a later cap set works retroactively.
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
            sprint_cap_tokens=cap,
        )


def get_settings() -> Settings:
    return Settings.from_env()
