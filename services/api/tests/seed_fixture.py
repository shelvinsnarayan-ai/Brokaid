"""CI fixture seed — 2 tenants, ~50 events each of mixed types."""
from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

from app.kernel.trail import append, create_tenant


async def seed():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    dsn = os.environ["DATABASE_URL"]
    from app.migrate import apply_migrations
    await apply_migrations(dsn)

    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=2)
    try:
        for slug in ["fixture-a", "fixture-b"]:
            # If tenant already exists, skip.
            async with pool.acquire() as c:
                row = await c.fetchrow("SELECT 1 FROM tenants WHERE slug=$1", slug)
                if row:
                    continue
            tenant, _ = await create_tenant(pool, slug=slug, display_name=slug.title())
            matter_id = str(uuid.uuid4())
            client_id = str(uuid.uuid4())
            await append(
                pool, tenant.tenant_id, "MatterCreated",
                actor={"kind": "human", "id": "u1", "name": "Broker"},
                payload={"matter_id": matter_id, "client_id": client_id,
                         "status": "new", "subject": f"Fixture matter for {slug}"},
                idempotency_key=f"mc:{matter_id}",
            )
            for i in range(48):
                if i % 4 == 0:
                    await append(
                        pool, tenant.tenant_id, "MatterStatusChanged",
                        actor={"kind": "agent", "id": "gAIte", "name": "policy"},
                        payload={"matter_id": matter_id, "status": f"phase-{i}"},
                        idempotency_key=f"msc:{matter_id}:{i}",
                    )
                else:
                    await append(
                        pool, tenant.tenant_id, "NoteRecorded",
                        actor={"kind": "human", "id": "u1", "name": "Broker"},
                        payload={"i": i, "text": f"note #{i}"},
                        idempotency_key=f"note:{matter_id}:{i}",
                    )
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(seed())
