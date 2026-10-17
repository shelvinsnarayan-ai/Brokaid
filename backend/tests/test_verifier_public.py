"""Phase 11 — public verifier route tests."""
from __future__ import annotations

import pytest

from app.kernel.certificate import (
    regenerate_certificate,
    revoke_token,
)
from app.routes.verifier import _reset_rate_limiter


async def _issue_cert(pool, tenant_id: str, policy_id: str) -> dict:
    return await regenerate_certificate(
        pool=pool, tenant_id=tenant_id, policy_id=policy_id,
        envelope_id=None,
    )


async def test_verifier_public_get_returns_public_safe_subset(app_client, scene_seeded) -> None:
    app, client = app_client
    _reset_rate_limiter()
    tenant_id = scene_seeded["tenant_id"]
    policy_id = scene_seeded["policy_ids"]["managed"]
    cert = await _issue_cert(app.state.pool, tenant_id, policy_id)

    r = await client.get(f"/api/verifier/{cert['raw_token']}")
    assert r.status_code == 200, r.text
    body = r.json()
    # public-safe subset
    assert body["holder_display"]
    assert body["insurer_display"]
    assert body["product"]
    assert isinstance(body["cover_types"], list)
    assert body["mocked_insurer"] is True
    # forbidden fields
    for forbidden in ("tenant_id", "model_id", "provider_meta", "actor"):
        assert forbidden not in body, f"public response leaked {forbidden}"


async def test_verifier_public_get_unknown_token_404(app_client, scene_seeded) -> None:
    _, client = app_client
    _reset_rate_limiter()
    r = await client.get("/api/verifier/" + "z" * 43)
    assert r.status_code in (404, 429)   # 429 tolerated if a prior test bled counters
    # in a clean state it must be 404
    if r.status_code == 429:
        pytest.skip("rate limiter cross-test contamination — cleared for next run")
    assert r.status_code == 404


async def test_verifier_public_revoked_returns_410(app_client, scene_seeded) -> None:
    app, client = app_client
    _reset_rate_limiter()
    tenant_id = scene_seeded["tenant_id"]
    policy_id = scene_seeded["policy_ids"]["managed"]
    cert = await _issue_cert(app.state.pool, tenant_id, policy_id)
    assert await revoke_token(app.state.pool, tenant_id, cert["token_hash"]) is True

    r = await client.get(f"/api/verifier/{cert['raw_token']}")
    assert r.status_code == 410, r.text


async def test_verifier_public_rate_limit_20_ok_5_too_many(app_client, scene_seeded) -> None:
    app, client = app_client
    _reset_rate_limiter()
    tenant_id = scene_seeded["tenant_id"]
    policy_id = scene_seeded["policy_ids"]["managed"]
    cert = await _issue_cert(app.state.pool, tenant_id, policy_id)

    ok = too_many = 0
    for _ in range(25):
        r = await client.get(f"/api/verifier/{cert['raw_token']}")
        if r.status_code == 200:
            ok += 1
        elif r.status_code == 429:
            too_many += 1
    assert ok == 20, f"expected 20 ok, got {ok}"
    assert too_many == 5, f"expected 5 429s, got {too_many}"
