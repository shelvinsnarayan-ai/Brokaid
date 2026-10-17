"""R1.1 — standalone job worker process (extra replicas, or the two-worker test).
Usage: python -m app.jobs.worker [--workers N] [--seconds S] [--db NAME] [--no-scheduler]
"""
from __future__ import annotations

import argparse
import asyncio
import os

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient


async def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--seconds", type=float, default=0, help="stop after S seconds (0 = forever)")
    ap.add_argument("--db", default=os.environ["DB_NAME"])
    ap.add_argument("--no-scheduler", action="store_true")
    a = ap.parse_args()
    from app.kernel import job_handlers  # noqa: F401
    from app.kernel import jobs
    store = AsyncIOMotorClient(os.environ["MONGO_URL"])[a.db]
    await jobs.ensure_indexes(store)
    await jobs.sweep(store)
    tag = jobs.worker_id("proc")
    tasks = [asyncio.create_task(jobs.worker_loop(store, f"{tag}:{i}")) for i in range(a.workers)]
    if not a.no_scheduler:
        tasks.append(asyncio.create_task(jobs.scheduler_loop(store, tag)))
    try:
        if a.seconds:
            await asyncio.sleep(a.seconds)
        else:
            await asyncio.gather(*tasks)
    finally:
        for t in tasks:
            t.cancel()


if __name__ == "__main__":
    asyncio.run(main())
