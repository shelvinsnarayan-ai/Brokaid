"""Response scrubber — strip `model_id` from any dict returned to the API.

Belt-and-braces guard for ground rule 7: model IDs live in ledger event
payloads and provider_meta, but must never render on operator/client
API responses.
"""
from __future__ import annotations

from typing import Any


_SCRUB_KEYS = ("model_id", "provider_meta")


def scrub(obj: Any) -> Any:
    """Recursively strip forbidden keys from any dict / list."""
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items() if k not in _SCRUB_KEYS}
    if isinstance(obj, list):
        return [scrub(x) for x in obj]
    return obj
