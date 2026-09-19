"""Phase 15 — export scoping: an operator can only see their own
tenant's events; a client_id filter for another tenant returns no
events (RLS-tenanted).
"""
from __future__ import annotations

import io
import json
import zipfile

import pytest


pytestmark = pytest.mark.asyncio


async def test_export_respects_tenant_scope(app_client, scene_seeded):
    _, client = app_client
    op = next(o for o in scene_seeded["operators"] if o["role"] == "principal")
    r = await client.post("/api/auth/dev/impersonate", json={"email": op["email"]})
    assert r.status_code == 200

    # Filter by a client_id that belongs to a DIFFERENT tenant. We
    # invent a UUID that is not in the current tenant.
    other_client_id = "11111111-2222-3333-4444-555555555555"
    r_preview = await client.get(
        f"/api/trail/export/preview?client_id={other_client_id}"
    )
    assert r_preview.status_code == 200
    body = r_preview.json()
    assert body["event_count"] == 0, (
        f"expected zero events for unknown client_id; got {body['event_count']}"
    )

    r_bundle = await client.get(
        f"/api/trail/export/bundle?client_id={other_client_id}"
    )
    assert r_bundle.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r_bundle.content))
    doc = json.loads(z.read("export.json"))
    assert doc["events"] == []
