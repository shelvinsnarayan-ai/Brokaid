"""JSON Schema export for the phase-3 contracts.

The three schemas are the single source of truth for cross-language
consumers. Run:

    python -m app.kernel.contracts.schemas > /tmp/out.json

or use the packaged script `scripts/export_schemas.py` which writes
them to `packages/kernel/contracts/schemas/`.
"""
from __future__ import annotations

import json
from typing import Any

from app.kernel.contracts.models import Approval, ContextEnvelope, OutputContract


def all_schemas() -> dict[str, dict[str, Any]]:
    return {
        "ContextEnvelope": ContextEnvelope.model_json_schema(),
        "OutputContract": OutputContract.model_json_schema(),
        "Approval": Approval.model_json_schema(),
    }


if __name__ == "__main__":
    print(json.dumps(all_schemas(), indent=2, sort_keys=True))
