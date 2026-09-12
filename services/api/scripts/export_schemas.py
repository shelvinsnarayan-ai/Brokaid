"""Export brAIn contract JSON schemas to packages/kernel/contracts/schemas/.

Idempotent. Overwrites existing files.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure `services/api/` is on sys.path so `import app.*` works when
# this script is run directly.
_API_ROOT = Path(__file__).resolve().parents[1]
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from app.kernel.contracts.schemas import all_schemas  # noqa: E402

OUT_DIR = Path(__file__).resolve().parents[3] / "packages" / "kernel" / "contracts" / "schemas"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, schema in all_schemas().items():
        path = OUT_DIR / f"{name}.schema.json"
        path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
