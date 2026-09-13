"""Phase 15 — parametrised RBAC guard over every currently-mounted
/api/o/* route.

Client dev-impersonated session → 403 on every /o/* endpoint.
Operator dev-impersonated session → NOT 403 (200 / 404 / 200 is fine).
"""
from __future__ import annotations

import pytest


pytestmark = pytest.mark.asyncio


# (method, path, expected non-403 codes for operator)
OPERATOR_ROUTES = [
    ("GET",  "/api/o/queue",                          {200}),
    ("GET",  "/api/o/kpi/summary",                    {200}),
    ("GET",  "/api/o/shadow-mode/summary",            {200}),
    ("GET",  "/api/o/shadow-mode/export?format=csv",  {200}),
    ("GET",  "/api/trail/export/preview",             {200}),
]


@pytest.mark.parametrize("method,path,ok_codes", OPERATOR_ROUTES)
async def test_client_user_gets_403(
    app_client, scene_seeded, method, path, ok_codes,
):
    _, client = app_client
    owner = next(
        c for c in scene_seeded["clients"] if c["role"] == "client_owner"
    )
    r = await client.post("/api/auth/dev/impersonate", json={"email": owner["email"]})
    assert r.status_code == 200, r.text
    fn = getattr(client, method.lower())
    r = await fn(path)
    assert r.status_code == 403, (
        f"{method} {path} — client user should get 403, got {r.status_code}: {r.text}"
    )


@pytest.mark.parametrize("method,path,ok_codes", OPERATOR_ROUTES)
async def test_operator_user_is_admitted(
    app_client, scene_seeded, method, path, ok_codes,
):
    _, client = app_client
    op = next(
        o for o in scene_seeded["operators"] if o["role"] == "principal"
    )
    r = await client.post("/api/auth/dev/impersonate", json={"email": op["email"]})
    assert r.status_code == 200, r.text
    fn = getattr(client, method.lower())
    r = await fn(path)
    assert r.status_code != 403, (
        f"{method} {path} — operator should NOT be 403, got {r.status_code}: {r.text}"
    )
