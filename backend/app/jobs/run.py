"""CLI entry point for the bounded-job runner.

    python -m app.jobs.run <job_name>

Exits 0 on success (or when another instance holds the lock), 1 on
failure, 2 on unknown job.

R5c-3: honours `BROKAID_LEGACY_PG`. When true (and `MONGO_URL`
is set), opens an pool. Otherwise opens a Motor client
against `MONGO_URL` / `DB_NAME`. Same lock semantics under either.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from app.jobs.chain_verify import _close_store, _open_store
from app.jobs.runner import JOBS, run_job

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
)


async def _main(job_name: str) -> int:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    store = await _open_store()
    try:
        return await run_job(store, job_name)
    finally:
        await _close_store(store)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            "usage: python -m app.jobs.run <job_name>\n"
            f"registered: {', '.join(sorted(JOBS))}",
            file=sys.stderr,
        )
        sys.exit(2)
    name = sys.argv[1]
    if name not in JOBS:
        print(
            f"unknown job: {name}. registered: {', '.join(sorted(JOBS))}",
            file=sys.stderr,
        )
        sys.exit(2)
    sys.exit(asyncio.run(_main(name)))
