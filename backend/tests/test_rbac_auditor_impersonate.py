"""Phase-18 RBAC negative: the auditor seat cannot log in.

Asserts the DEMO_CHEATSHEET's "RBAC-negative moment" beat holds:
`POST /api/auth/dev/impersonate {"email": "auditor@harbourline.example"}`
returns 403 with detail `no_login_capable_role`, and the auditor's
seeded role is confirmed to be in `DATA_ONLY_ROLES` (never
`LOGIN_CAPABLE_ROLES`).
"""
from __future__ import annotations

import pytest

from app.kernel.rbac import DATA_ONLY_ROLES, LOGIN_CAPABLE_ROLES


@pytest.mark.asyncio
async def test_auditor_impersonate_returns_403(app_client, seeded):
    """The exact click-path the DEMO_CHEATSHEET points at must 403."""
    _, client = app_client
    r = await client.post(
        "/api/auth/dev/impersonate",
        json={"email": "auditor@harbourline.example"},
    )
    assert r.status_code == 403, (
        f"auditor must 403 on dev-impersonate; got {r.status_code} → {r.text}"
    )
    assert r.json() == {"detail": "no_login_capable_role"}


def test_auditor_role_is_data_only() -> None:
    """Belt-and-braces: `auditor` is in `DATA_ONLY_ROLES`, never in
    `LOGIN_CAPABLE_ROLES`. The impersonate route filters by the login-
    capable set, so this static-role assertion locks the RBAC-negative
    demo beat at the source. If someone ever moves `auditor` into
    `LOGIN_CAPABLE_ROLES` this test flips red before the 403 beat can
    regress silently."""
    assert "auditor" in DATA_ONLY_ROLES
    assert "auditor" not in LOGIN_CAPABLE_ROLES
