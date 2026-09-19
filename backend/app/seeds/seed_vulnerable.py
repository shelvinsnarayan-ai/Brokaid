"""Phase 11 — vulnerable-flag seed. Adds Kelp Shipping Co. Pty Ltd
(fictional) with `vulnerable_flag=true` and a Managed policy on it.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone


from app.kernel.trail import append


VULN_CLIENT_ID = "00000000-0000-4000-8000-000000000003"
VULN_CLIENT_NAME = "Kelp Shipping Co. Pty Ltd"
VULN_POLICY_ID = "00000000-0000-4000-8000-000000000103"


async def seed(pool: object, tenant_id: str) -> dict[str, str]:
    now = datetime.now(timezone.utc)
    inception = now
    expiry = now + timedelta(days=365)
    schedule = {
        "cover_types": [
            {"type": "Marine Cargo", "limit_any_one": 5_000_000,
             "aggregate": 15_000_000, "currency": "AUD"},
        ],
        "excess": {"any_one_occurrence": 10_000, "currency": "AUD"},
        "territorial_limits": "Worldwide",
    }
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            await c.execute(
                """
                INSERT INTO clients (client_id, tenant_id, display_name,
                                     industry, domicile, jurisdiction_code,
                                     vulnerable_flag)
                VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, TRUE)
                ON CONFLICT (client_id) DO UPDATE SET vulnerable_flag = TRUE
                """,
                VULN_CLIENT_ID, tenant_id, VULN_CLIENT_NAME,
                "shipping", "Melbourne, AU", "AU",
            )
            await c.execute(
                """
                INSERT INTO policies (policy_id, tenant_id, client_id, status,
                                      product, insurer_display, inception, expiry,
                                      premium_amount, premium_currency, schedule,
                                      imported_from)
                VALUES ($1::uuid, $2::uuid, $3::uuid, 'managed',
                        'Marine Cargo', 'Fictional Carrier B', $4, $5,
                        4200.00, 'AUD', $6::jsonb, NULL)
                ON CONFLICT (policy_id) DO NOTHING
                """,
                VULN_POLICY_ID, tenant_id, VULN_CLIENT_ID,
                inception, expiry, json.dumps(schedule),
            )
    try:
        await append(
            pool, tenant_id, "ClientEntityCreated",
            actor={"kind": "system", "id": "seed", "name": "phase11-seed"},
            payload={
                "client_id": VULN_CLIENT_ID,
                "display_name": VULN_CLIENT_NAME,
                "industry": "shipping",
                "domicile": "Melbourne, AU",
                "jurisdiction_code": "AU",
                "vulnerable_flag": True,
                "fictional": True,
            },
            idempotency_key=f"seed:client:{VULN_CLIENT_ID}",
        )
    except Exception:
        pass
    try:
        await append(
            pool, tenant_id, "PolicyCreated",
            actor={"kind": "system", "id": "seed", "name": "phase11-seed"},
            payload={
                "policy_id": VULN_POLICY_ID,
                "client_id": VULN_CLIENT_ID,
                "status": "managed",
                "product": "Marine Cargo",
                "insurer_display": "Fictional Carrier B",
                "premium_amount": 4200.0,
                "premium_currency": "AUD",
                "fictional": True,
            },
            idempotency_key=f"seed:policy:{VULN_POLICY_ID}",
        )
    except Exception:
        pass

    return {"client_id": VULN_CLIENT_ID, "policy_id": VULN_POLICY_ID}
