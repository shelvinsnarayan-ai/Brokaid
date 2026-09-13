"""Phase 13 — notification dev-mode path.

* `EmailRenderedDevMode` emitted with a filesystem path.
* HTML file exists at reported path.
* `InAppNotification` emitted with matching source_event_id.
* Payload text is equivalent (email text ↔ in-app body).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


pytestmark = pytest.mark.asyncio


async def test_notification_dev_mode(app_client, scene_seeded, pool, monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)

    app, client = app_client
    client_owner = next(
        c for c in scene_seeded["clients"] if c["role"] == "client_owner"
    )
    r = await client.post(
        "/api/auth/dev/impersonate", json={"email": client_owner["email"]},
    )
    assert r.status_code == 200
    r = await client.post(
        "/api/c/claims",
        json={
            "incident_date": "2026-09-01",
            "description": "Broken window in the storeroom. No injuries.",
        },
    )
    assert r.status_code == 200
    claim_ref = r.json()["claim_reference"]

    tenant_id = scene_seeded["tenant_id"]
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            email_ev = await c.fetchrow(
                "SELECT payload FROM events "
                " WHERE tenant_id=$1::uuid AND type='EmailRenderedDevMode' "
                " ORDER BY seq DESC LIMIT 1", tenant_id,
            )
            inapp_ev = await c.fetchrow(
                "SELECT payload FROM events "
                " WHERE tenant_id=$1::uuid AND type='InAppNotification' "
                " ORDER BY seq DESC LIMIT 1", tenant_id,
            )
    assert email_ev is not None
    assert inapp_ev is not None
    email_p = (
        json.loads(email_ev["payload"]) if isinstance(email_ev["payload"], str)
        else email_ev["payload"]
    )
    inapp_p = (
        json.loads(inapp_ev["payload"]) if isinstance(inapp_ev["payload"], str)
        else inapp_ev["payload"]
    )
    # File exists at reported path.
    assert email_p["path"], "EmailRenderedDevMode must include a path"
    assert Path(email_p["path"]).exists(), (
        f"rendered email file missing at {email_p['path']}"
    )
    # Text form (sibling .txt).
    text_path = Path(email_p["path"]).with_suffix(".txt")
    assert text_path.exists()
    # Both events reference the SAME source_event_id and same template_payload.
    assert email_p["source_event_id"] == inapp_p["source_event_id"]
    assert email_p["template_payload"] == inapp_p["template_payload"]
    # In-app subject matches email subject (equivalence audit).
    assert email_p["subject"] == inapp_p["subject"]
    assert claim_ref in email_p["subject"]
