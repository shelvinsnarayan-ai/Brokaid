"""Flatten an app's routes (FastAPI ≥0.140 wraps included routers in lazy _IncludedRouter objects)."""
from __future__ import annotations


def all_routes(app_or_router) -> list:
    out = []
    for r in getattr(app_or_router, "routes", []):
        inner = getattr(r, "original_router", None)
        out.extend(all_routes(inner) if inner is not None else [r])
    return out
