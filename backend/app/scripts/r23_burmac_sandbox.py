"""R2.3 — create/refresh the "burmac-sandbox" TEST tenant on a dev/preview database. Refuses in production.

    cd /app/backend && python -m app.scripts.r23_burmac_sandbox
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app.db import mongo as mongo_db  # noqa: E402
from app.kernel import devmode, object_store, sandbox  # noqa: E402


async def main() -> None:
    if not devmode.dev_mode():
        raise SystemExit("Refusing: the sandbox is for dev/preview only.")
    await mongo_db.init_mongo_client()
    object_store.init_storage()
    print(await sandbox.ensure(mongo_db.get_db_sync()))


if __name__ == "__main__":
    asyncio.run(main())
