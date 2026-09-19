"""R5c-4a — Mongo-only db package.

Postgres runtime deleted. Re-exports the Mongo module + tenant-safe
wrapper for callers that still `from app import db` or
`from app.db import mongo, tenant_safe`.
"""
from __future__ import annotations

from app.db import mongo  # noqa: F401
from app.db import tenant_safe  # noqa: F401

__all__ = ["mongo", "tenant_safe"]
