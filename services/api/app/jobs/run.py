"""CLI entry point for the bounded-job runner.

    python -m app.jobs.run <job_name>

Exits 0 on success (or when another instance holds the lock), 1 on
failure, 2 on unknown job.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

from app.jobs.runner import JOBS, run_job

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)


async def _main(job_name: str) -> int:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    dsn = os.environ["DATABASE_URL"]
    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=2)
    try:
        return await run_job(pool, job_name)
    finally:
        await pool.close()


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
