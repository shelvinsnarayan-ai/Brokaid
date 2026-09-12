"""Phase-5 client-entity seed — Harbour Marine Services Pty Ltd (fictional)."""
from __future__ import annotations

import asyncpg

from app.kernel.trail import append


CLIENT_ID = "00000000-0000-4000-8000-000000000001"
CLIENT_NAME = "Harbour Marine Services Pty Ltd"
INDUSTRY = "marine_services"
DOMICILE = "Sydney, AU"
JURISDICTION = "AU"


async def seed(pool: asyncpg.Pool, tenant_id: str) -> str:
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            await c.execute(
                """
                INSERT INTO clients (client_id, tenant_id, display_name,
                                     industry, domicile, jurisdiction_code)
                VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6)
                ON CONFLICT (client_id) DO NOTHING
                """,
                CLIENT_ID, tenant_id, CLIENT_NAME, INDUSTRY, DOMICILE, JURISDICTION,
            )

    try:
        await append(
            pool, tenant_id, "ClientEntityCreated",
            actor={"kind": "system", "id": "seed", "name": "phase5-seed"},
            payload={
                "client_id": CLIENT_ID,
                "display_name": CLIENT_NAME,
                "industry": INDUSTRY,
                "domicile": DOMICILE,
                "jurisdiction_code": JURISDICTION,
                "fictional": True,
            },
            idempotency_key=f"seed:client:{CLIENT_ID}",
        )
    except asyncpg.UniqueViolationError:
        pass
    return CLIENT_ID
