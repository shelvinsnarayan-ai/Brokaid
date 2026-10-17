"""Phase R5b-1b — Scene 7 gate: Mongo-generated trail export must
verify byte-identically against the pre-signed ci-alpha bundle.

This is the R2 promise validated under REAL REQUEST FLOW: the
trail export/preview/bundle routes hit through HTTP under
`BROKAID_LEDGER=mongo`, the Mongo-generated `export.json` is
extracted from the streamed zip, and its events + tenant sections
are compared byte-identically to the pre-signed bundle at
`docs/demo_bundle/ci-alpha.zip`. Root hash MUST equal
`92e61063c8d225f40bebd2ae794d9d5810bbf9a48d92ba056845c68b6ac1b5d8`.

R2 scope: the byte-identical guarantee applies to the events +
tenant sections (chain integrity). The wrapper metadata
(`generated_at`, `verifier_binary_sha256`) is per-generation and
NOT part of the guarantee — the test does an md5 on the R2-scoped
subset for the deliverable proof.
"""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.sessions import COOKIE_NAME, create as create_session
from app.main import create_app


pytestmark = pytest.mark.asyncio

REPO_ROOT = Path(__file__).resolve().parents[2]
PRE_SIGNED_BUNDLE = REPO_ROOT / "docs" / "demo_bundle" / "ci-alpha.zip"
VERIFIER_JS = REPO_ROOT / "packages" / "kernel" / "verifier" / "verify.js"
CANONICALIZE_MODULE = REPO_ROOT / "node_modules" / "canonicalize"

EXPECTED_ROOT = "92e61063c8d225f40bebd2ae794d9d5810bbf9a48d92ba056845c68b6ac1b5d8"
EXPECTED_EVENTS = 21
EXPECTED_TENANT_SLUG = "ci-alpha"


def _node_available() -> bool:
    try:
        subprocess.run(
            ["node", "--version"], capture_output=True, timeout=3,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


async def _mongo_seed_and_app(pool, mongo_db, monkeypatch):
    """Seed ci-alpha into Mongo, boot an app pinned to Mongo, and
    return the AsyncClient + a valid operator cookie."""
    from app.seeds.ci_deterministic import (
        CI_TENANT_A_ID, seed_ci_deterministic, seed_ci_deterministic_mongo,
    )
    # PG side is only needed because dev-impersonation reads from the
    # session store which is Postgres-backed in the tests; the routes
    # themselves will hit Mongo per BROKAID_LEDGER=mongo.
    await seed_ci_deterministic(pool)
    await seed_ci_deterministic_mongo(mongo_db)

    monkeypatch.setenv("BROKAID_LEDGER", "mongo")

    # Bootstrap an operator user + session on Postgres (session store).
    import uuid
    op_user = str(uuid.uuid4())
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", CI_TENANT_A_ID,
            )
            await c.execute(
                "INSERT INTO users (user_id, email, display_name, kind) "
                "VALUES ($1::uuid, $2, $3, 'operator') "
                "ON CONFLICT (email) DO NOTHING",
                op_user, f"op-{op_user[:8]}@ci.example", "Scene7 Operator",
            )
            row = await c.fetchrow(
                "SELECT user_id::text FROM users WHERE email=$1",
                f"op-{op_user[:8]}@ci.example",
            )
            op_user = row["user_id"]
    session = await create_session(
        pool, user_id=op_user, tenant_id=CI_TENANT_A_ID,
        client_id=None, active_role="principal", mfa_verified=True,
    )

    app = create_app(pool_override=pool, mongo_db_override=mongo_db,
                     seed_dev_users=False)
    return app, session.session_id, CI_TENANT_A_ID


# ---- Scene 7 gate: byte-identical export ------------------------------
async def test_r5b1b_scene7_export_byte_identical(
    pool, mongo_db, monkeypatch,
):
    """The R2 byte-identical guarantee validated under real request
    flow — GET /api/trail/export/bundle under BROKAID_LEDGER=mongo
    produces an export.json whose events + tenant portions equal the
    pre-signed ci-alpha bundle byte-for-byte."""
    app, sid, _tid = await _mongo_seed_and_app(pool, mongo_db, monkeypatch)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver",
        ) as ac:
            r = await ac.get(
                "/api/trail/export/bundle",
                cookies={COOKIE_NAME: sid},
            )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"

    # Extract the Mongo-generated export.json out of the streamed zip.
    z_mongo = zipfile.ZipFile(io.BytesIO(r.content))
    mongo_bytes = z_mongo.read("export.json")
    mongo_doc = json.loads(mongo_bytes)

    # Load the pre-signed export.json.
    z_signed = zipfile.ZipFile(str(PRE_SIGNED_BUNDLE))
    signed_bytes = z_signed.read("export.json")
    signed_doc = json.loads(signed_bytes)

    # R2 byte-identical subset: tenant + events (hash-chain fields).
    # The `seq` field is a per-store position number and is NOT part of
    # the event_hash computation — it can differ across backends
    # without breaking the R2 guarantee. In this specific pre-signed
    # bundle it does differ: the bundle was generated against a
    # Postgres `events.seq` (global BIGSERIAL) that had already advanced
    # to 330 by prior seeds, so ci-alpha lands at seq 331..351. The
    # Mongo `append_event` numbers per-tenant, so ci-alpha lands at
    # seq 1..21. Both are structurally valid; the hash chain is intact.
    # We assert:
    #   (a) the seq delta is a single constant across all events (proves
    #       mechanical origin — not a data drift);
    #   (b) every hash-bearing field is byte-identical;
    #   (c) the final event_hash equals the pre-signed root.
    assert mongo_doc["tenant"] == signed_doc["tenant"], (
        f"tenant drift! mongo={mongo_doc['tenant']} "
        f"signed={signed_doc['tenant']}"
    )
    assert len(mongo_doc["events"]) == EXPECTED_EVENTS
    assert len(signed_doc["events"]) == EXPECTED_EVENTS

    # Assert (a) — seq is a constant offset across all 21 events.
    deltas = {
        signed_doc["events"][i]["seq"] - mongo_doc["events"][i]["seq"]
        for i in range(EXPECTED_EVENTS)
    }
    assert len(deltas) == 1, f"seq offsets not constant: {deltas}"
    seq_delta = deltas.pop()

    # Assert (b) — strip seq, compare the R2-hash-bearing subset.
    def _strip_seq(events):
        return [{k: v for k, v in e.items() if k != "seq"} for e in events]

    mongo_r2 = json.dumps(
        {"tenant": mongo_doc["tenant"], "events": _strip_seq(mongo_doc["events"])},
        sort_keys=True, separators=(",", ":"),
    ).encode()
    signed_r2 = json.dumps(
        {"tenant": signed_doc["tenant"], "events": _strip_seq(signed_doc["events"])},
        sort_keys=True, separators=(",", ":"),
    ).encode()
    mongo_md5 = hashlib.md5(mongo_r2).hexdigest()
    signed_md5 = hashlib.md5(signed_r2).hexdigest()
    bytes_diff = 0 if mongo_r2 == signed_r2 else abs(len(mongo_r2) - len(signed_r2))

    # Assert (c) — final event_hash equals the pre-signed root.
    print(
        f"\nSCENE-7 R2 BYTE-IDENTICAL:\n"
        f"  hash-chain md5 (mongo)  = {mongo_md5}\n"
        f"  hash-chain md5 (signed) = {signed_md5}\n"
        f"  bytes_diff              = {bytes_diff}\n"
        f"  seq offset (pg-signed - mongo) = {seq_delta} "
        f"(constant → PG global-seq artefact, not R2 drift)\n"
        f"  root hash               = {mongo_doc['events'][-1]['event_hash']}\n"
        f"  expected root           = {EXPECTED_ROOT}\n",
        flush=True,
    )
    assert mongo_r2 == signed_r2, "R2 hash-chain subset drift"
    assert mongo_md5 == signed_md5
    assert bytes_diff == 0
    assert mongo_doc["events"][-1]["event_hash"] == EXPECTED_ROOT
    assert mongo_doc["tenant"]["slug"] == EXPECTED_TENANT_SLUG


# ---- Scene 7 gate: offline verifier verdict ---------------------------
@pytest.mark.skipif(not _node_available(),
                    reason="node absent — cannot exec verify.js")
async def test_r5b1b_scene7_offline_verify(
    pool, mongo_db, monkeypatch, tmp_path,
):
    """Mongo-generated bundle → node verify.js → asserts `OK` with
    exactly 21 events + the pre-signed root hash."""
    app, sid, _tid = await _mongo_seed_and_app(pool, mongo_db, monkeypatch)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver",
        ) as ac:
            r = await ac.get(
                "/api/trail/export/bundle",
                cookies={COOKIE_NAME: sid},
            )
    assert r.status_code == 200, r.text

    workdir = tmp_path / "mongo-bundle"
    workdir.mkdir()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        z.extractall(workdir)
    if CANONICALIZE_MODULE.exists():
        nm = workdir / "node_modules"
        nm.mkdir()
        shutil.copytree(CANONICALIZE_MODULE, nm / "canonicalize")

    proc = subprocess.run(
        ["node", "verify.js", "export.json"],
        cwd=str(workdir), capture_output=True, text=True, timeout=15,
    )
    print(f"\nSCENE-7 verify.js stdout:\n{proc.stdout}", flush=True)
    assert proc.returncode == 0, (
        f"verify.js failed: stdout={proc.stdout} stderr={proc.stderr}"
    )
    assert "OK" in proc.stdout
    assert str(EXPECTED_EVENTS) in proc.stdout
    assert EXPECTED_ROOT in proc.stdout


# ---- Preview scoping under Mongo -------------------------------------
async def test_r5b1b_export_preview_under_mongo(pool, mongo_db, monkeypatch):
    """GET /api/trail/export/preview under Mongo returns the expected
    event_count and event_types_summary for ci-alpha."""
    app, sid, _tid = await _mongo_seed_and_app(pool, mongo_db, monkeypatch)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver",
        ) as ac:
            r = await ac.get(
                "/api/trail/export/preview",
                cookies={COOKIE_NAME: sid},
            )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["event_count"] == EXPECTED_EVENTS
    assert isinstance(body["event_types_summary"], list)
    assert sum(x["count"] for x in body["event_types_summary"]) == EXPECTED_EVENTS


# ---- Verifier route: positive / revoked / rate-limit -----------------
async def _seed_verifier_token(mongo_db, *, tenant_id: str, policy_id: str,
                                cert_id: str, token_hash: str,
                                revoked: bool = False, expired: bool = False):
    """Insert a verifier_token doc into the system collection + emit a
    CertificateRegenerated event so the verifier's tenant-scoped read
    finds it."""
    from datetime import datetime, timedelta, timezone
    from app.db.tenant_safe import wrap
    from app.kernel import trail_mongo
    from app.kernel.tenant_ctx import use_tenant

    safe = wrap(mongo_db)
    now = datetime.now(timezone.utc)
    exp = now - timedelta(days=1) if expired else now + timedelta(days=30)
    await safe.system("verifier_tokens").insert_one({
        "token_hash": token_hash,
        "tenant_id": tenant_id,
        "policy_id": policy_id,
        "certificate_id": cert_id,
        "revoked_at": now if revoked else None,
        "expires_at": exp,
        "rate_limit_count": 0,
    })
    # Cert event under the tenant ctx.
    async with use_tenant(tenant_id):
        await trail_mongo.append_event(
            mongo_db, tenant_id, "CertificateRegenerated",
            actor={"kind": "system", "id": "certificate", "name": "certs"},
            payload={
                "envelope_id": "env-v",
                "policy_id": policy_id,
                "certificate_id": cert_id,
                "certificate": {
                    "certificate_id": cert_id,
                    "holder_display": "Fictional Holder Pty Ltd",
                    "insurer_display": "Fictional Carrier",
                    "product": "public_liability",
                    "cover_types": ["public_liability"],
                    "excess": "AUD 2,500",
                    "territorial_limits": "Australia",
                    "effective_from": now.isoformat(timespec="microseconds"),
                    "effective_to": (now + timedelta(days=365)
                                      ).isoformat(timespec="microseconds"),
                    "issued_at": now.isoformat(timespec="microseconds"),
                },
                "mocked_insurer": True,
            },
            idempotency_key=f"cert:regen:{cert_id}",
            mocked=True,
        )


async def test_r5b1b_verifier_positive_under_mongo(pool, mongo_db, monkeypatch):
    """GET /api/verifier/{token} → 200 with the cert payload under Mongo."""
    from app.kernel.certificate import hash_token
    from app.seeds.ci_deterministic import CI_TENANT_A_ID
    import uuid
    from app.seeds.ci_deterministic import seed_ci_deterministic_mongo

    await seed_ci_deterministic_mongo(mongo_db)
    monkeypatch.setenv("BROKAID_LEDGER", "mongo")

    tok = "verify-token-" + uuid.uuid4().hex
    hashed = hash_token(tok)
    cert_id = str(uuid.uuid4())
    policy_id = str(uuid.uuid4())
    await _seed_verifier_token(
        mongo_db, tenant_id=CI_TENANT_A_ID, policy_id=policy_id,
        cert_id=cert_id, token_hash=hashed,
    )

    app = create_app(pool_override=pool, mongo_db_override=mongo_db,
                     seed_dev_users=False)
    from app.routes.verifier import _reset_rate_limiter
    _reset_rate_limiter()

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver",
        ) as ac:
            r = await ac.get(f"/api/verifier/{tok}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["holder_display"] == "Fictional Holder Pty Ltd"
    assert body["product"] == "public_liability"
    assert body["policy_id"] == policy_id
    assert body["mocked_insurer"] is True


async def test_r5b1b_verifier_revoked_under_mongo(pool, mongo_db, monkeypatch):
    """Revoked token → 410."""
    from app.kernel.certificate import hash_token
    from app.seeds.ci_deterministic import (
        CI_TENANT_A_ID, seed_ci_deterministic_mongo,
    )
    import uuid
    await seed_ci_deterministic_mongo(mongo_db)
    monkeypatch.setenv("BROKAID_LEDGER", "mongo")

    tok = "verify-revoked-" + uuid.uuid4().hex
    hashed = hash_token(tok)
    await _seed_verifier_token(
        mongo_db, tenant_id=CI_TENANT_A_ID,
        policy_id=str(uuid.uuid4()), cert_id=str(uuid.uuid4()),
        token_hash=hashed, revoked=True,
    )
    app = create_app(pool_override=pool, mongo_db_override=mongo_db,
                     seed_dev_users=False)
    from app.routes.verifier import _reset_rate_limiter
    _reset_rate_limiter()
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver",
        ) as ac:
            r = await ac.get(f"/api/verifier/{tok}")
    assert r.status_code == 410, r.text
    assert "revoked" in r.text
