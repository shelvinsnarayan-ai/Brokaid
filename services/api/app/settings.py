"""Runtime settings for the brokAId API.

All configuration comes from environment variables loaded via python-dotenv
in main.py. No defaults for URLs or credentials — missing config should
fail fast at startup.
"""
from __future__ import annotations

import os
from pydantic import BaseModel


class Settings(BaseModel):
    database_url: str
    cors_origins: list[str]
    auth_dev_mode: bool

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.environ["DATABASE_URL"],
            cors_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
            auth_dev_mode=os.environ.get("AUTH_DEV_MODE", "false").lower() == "true",
        )


def get_settings() -> Settings:
    return Settings.from_env()
