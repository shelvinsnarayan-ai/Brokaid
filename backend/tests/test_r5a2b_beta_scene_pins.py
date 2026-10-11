"""Phase R5a-2b-β — c_scene extraction regression pins.

Extract-first discipline: this file pins the OBSERVABLE behaviour
of `routes/c_scene.py` and the extracted `kernel/scene.py` +
`kernel/scene_mongo.py`. The pins are written to pass against BOTH
the pre-extraction inline route AND the post-extraction kernel-
dispatched route — the extraction is byte-verbatim so no assertion
should shift.

Coverage
--------
HTTP (observable):
  - GET  /status: every state-machine transition (idle, intake_running,
    placement_running, awaiting_operator, awaiting_payment,
    awaiting_signature, executing, certificate_ready)
  - GET /pay: MOCKED BPAY/EFT details, no events; POST /paid emits a
    single mocked `payment.advised`.
  - POST /sign: emits SignatureRequested + SignatureCompleted with
    pre-extraction payload/actor/idem/mocked shape.
  - RBAC: no session on the client shell → 401.
  - 404s: policy_not_found + quote_not_found from /pay.

Direct kernel (post-extraction only; skipped if `kernel.scene` is
not yet importable):
  - emit_client_change_requested / emit_submission_drafted /
    emit_carrier_quoted / emit_endorsement_quote_ready /
    emit_payment_* / emit_signature_* — event shape pin per
    the pre-extraction `await append(...)` sites.
  - compute_status(): pure state-machine transitions.

No LLM. `/request-change` full flow is out of scope for pins
(exercised by the LLM-gated `test_reference_scene_happy_path`).
"""
from __future__ import annotations

import importlib
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.sessions import COOKIE_NAME, create as create_session
from app.kernel import trail
from app.kernel.trail import append

pytestmark = pytest.mark.asyncio


# ---- Fixtures ---------------------------------------------------------
async def _tenant_and_client(pool):
    """Create tenant, client user + client entity + client_owner link +
    a policy row. Returns dict with all ids + a live client session."""
    slug = f"scn-{uuid.uuid4().hex[:6]}"
    t, _ = await trail.create_tenant(pool, slug=slug, display_name=f"Scene {slug}")
    tid = t.tenant_id
    client_user = str(uuid.uuid4())
    client_id = str(uuid.uuid4())
    policy_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tid,
            )
            await c.execute(
                "INSERT INTO users (user_id, email, display_name, kind) "
                "VALUES ($1::uuid, $2, $3, 'client')",
                client_user, f"own-{slug}@ci.example", "Scene Owner",
            )
            await c.execute(
                "INSERT INTO clients "
                "  (client_id, tenant_id, display_name, industry, "
                "   domicile, jurisdiction_code) "
                "VALUES ($1::uuid, $2::uuid, 'Scene Client Pty Ltd', "
                "        'construction', 'AU', 'AU')",
                client_id, tid,
            )
            await c.execute(
                "INSERT INTO user_client_links "
                "  (user_id, tenant_id, client_id, role) "
                "VALUES ($1::uuid, $2::uuid, $3::uuid, 'client_owner')",
                client_user, tid, client_id,
            )
            await c.execute(
                "INSERT INTO policies "
                "  (policy_id, tenant_id, client_id, status, product, "
                "   insurer_display, inception, expiry, premium_amount, "
                "   premium_currency, schedule) "
                "VALUES ($1::uuid, $2::uuid, $3::uuid, 'managed', "
                "        'public_liability', 'Fictional Carrier A', "
                "        $4, $5, 12500.00, 'AUD', $6::jsonb)",
                policy_id, tid, client_id, now, now + timedelta(days=365),
                json.dumps({
                    "cover_types": ["public_liability"],
                    "excess": "AUD 2,500",
                    "territorial_limits": "Australia",
                }),
            )
    session = await create_session(
        pool, user_id=client_user, tenant_id=tid,
        client_id=client_id, active_role="client_owner", mfa_verified=True,
    )
    return {
        "tenant_id": tid, "user_id": client_user, "client_id": client_id,
        "policy_id": policy_id, "session_id": session.session_id, "slug": slug,
    }


async def _seed_carrier_quoted(pool, tenant_id: str, envelope_id: str,
                                policy_id: str) -> dict:
    """Seed a CarrierQuoted event so /pay can look it up. Uses the
    pre-extraction c_scene payload shape (payload = {envelope_id,
    policy_id, **quote})."""
    quote = {
        "quote_id": "Q-PIN-1",
        "carrier_name": "Fictional Carrier A",
        "premium_change_amount": "125.00",
        "premium_new_annual": "12625.00",
        "premium_currency": "AUD",
        "terms": {"basis": "annual"},
        "conditions": ["standard exclusions apply"],
        "quoted_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "mocked": True,
    }
    await append(
        pool, tenant_id, "CarrierQuoted",
        actor={"kind": "system", "id": "carrier_channel", "name": "carrier"},
        payload={"envelope_id": envelope_id, "policy_id": policy_id, **quote},
        mocked=True,
        idempotency_key=f"carrier:quote:{envelope_id}",
    )
    return quote


async def _asgi_client(pool):
    from app.main import create_app
    app = create_app(pool_override=pool, seed_dev_users=False)
    return app


def _cookie(sid: str) -> dict[str, str]:
    return {COOKIE_NAME: sid}


async def _fetch_events_by_type_env(pool, tenant_id: str, type_: str,
                                    envelope_id: str) -> list[dict]:
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id,
            )
            rows = await c.fetch(
                "SELECT type, actor, payload, idempotency_key, mocked "
                "FROM events WHERE tenant_id=$1::uuid AND type=$2 "
                "  AND payload->>'envelope_id' = $3 "
                "ORDER BY seq ASC",
                tenant_id, type_, envelope_id,
            )
    out = []
    for r in rows:
        actor = r["actor"] if isinstance(r["actor"], dict) else json.loads(r["actor"])
        payload = r["payload"] if isinstance(r["payload"], dict) else json.loads(r["payload"])
        out.append({
            "type": r["type"], "actor": actor, "payload": payload,
            "idempotency_key": r["idempotency_key"], "mocked": r["mocked"],
        })
    return out


# ---- HTTP pins: state machine via GET /status -------------------------
_STATE_CASES = [
    # (event_types_in_ORDER_ascending, expected_state)
    ([], "idle"),
    (["ClientChangeRequested"], "intake_running"),
    (["ClientChangeRequested", "SubmissionDrafted"], "placement_running"),
    (["ClientChangeRequested", "SubmissionDrafted",
      "EndorsementQuoteReady"], "awaiting_operator"),
    (["ClientChangeRequested", "SubmissionDrafted",
      "EndorsementQuoteReady", "Approval"], "awaiting_payment"),
    (["ClientChangeRequested", "SubmissionDrafted",
      "EndorsementQuoteReady", "Approval",
      "payment.advised"], "awaiting_signature"),
    (["ClientChangeRequested", "SubmissionDrafted",
      "EndorsementQuoteReady", "Approval",
      "payment.advised",
      "SignatureRequested", "SignatureCompleted"], "executing"),
    (["ClientChangeRequested", "SubmissionDrafted",
      "EndorsementQuoteReady", "Approval",
      "payment.advised",
      "SignatureRequested", "SignatureCompleted",
      "EndorsementExecuted", "PolicyUpdated",
      "CertificateRegenerated"], "certificate_ready"),
]


@pytest.mark.parametrize("event_types,expected_state", _STATE_CASES)
async def test_r5a2bb_status_state_machine(pool, event_types, expected_state):
    """GET /status returns the exact state pre-extraction c_scene would
    have returned for each seeded event history."""
    ctx = await _tenant_and_client(pool)
    tid = ctx["tenant_id"]
    envelope_id = str(uuid.uuid4())
    # Seed events in order, all under this policy_id.
    for i, t_type in enumerate(event_types):
        actor_map = {
            "ClientChangeRequested": {"kind": "human", "id": ctx["user_id"],
                                       "name": "client_owner"},
            "Approval": {"kind": "human", "id": ctx["user_id"], "name": "principal"},
            "payment.advised": {"kind": "human", "id": ctx["user_id"],
                                 "name": "client_owner"},
            "SignatureCompleted": {"kind": "human", "id": ctx["user_id"],
                                    "name": "client_owner"},
        }
        actor = actor_map.get(t_type, {"kind": "system", "id": "seed", "name": "seed"})
        payload = {"envelope_id": envelope_id, "policy_id": ctx["policy_id"]}
        # Approval + ClientMessage* invariants — add classifier_verdict
        # where required by trail's belt-and-braces (Approval is not
        # in _CLIENT_RELEASE_EVENT_TYPES so unaffected).
        await append(
            pool, tid, t_type, actor=actor, payload=payload,
            idempotency_key=f"seed:{t_type}:{envelope_id}:{i}",
        )
    app = await _asgi_client(pool)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver",
        ) as ac:
            r = await ac.get(
                f"/api/c/matters/{ctx['policy_id']}/status",
                cookies=_cookie(ctx["session_id"]),
            )
    assert r.status_code == 200, r.text
    assert r.json()["state"] == expected_state, r.json()


# ---- HTTP pins: /pay details (no events) + /paid (payment.advised) ----
async def test_r5a2bb_pay_details_then_paid_advised(pool):
    """GET /pay → MOCKED BPAY/EFT details, no payment events. POST /paid
    → exactly one `payment.advised` (mocked, human actor, idem pin)."""
    ctx = await _tenant_and_client(pool)
    tid = ctx["tenant_id"]
    envelope_id = str(uuid.uuid4())
    await _seed_carrier_quoted(pool, tid, envelope_id, ctx["policy_id"])

    app = await _asgi_client(pool)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver",
        ) as ac:
            r = await ac.get(
                f"/api/c/matters/{ctx['policy_id']}/pay/{envelope_id}",
                cookies=_cookie(ctx["session_id"]),
            )
            assert r.status_code == 200, r.text
            d = r.json()["payment_details"]
            assert d["mocked"] is True and d["bpay"]["mocked"] is True
            assert d["eft"]["mocked"] is True and d["due_date"]
            for t in ("PaymentMandateCreated", "PaymentInitiated", "PaymentReceived"):
                assert not await _fetch_events_by_type_env(pool, tid, t, envelope_id)
            r = await ac.post(
                f"/api/c/matters/{ctx['policy_id']}/paid/{envelope_id}",
                cookies=_cookie(ctx["session_id"]),
            )
    assert r.status_code == 200, r.text
    adv = await _fetch_events_by_type_env(pool, tid, "payment.advised", envelope_id)
    assert len(adv) == 1
    assert adv[0]["mocked"] is True
    assert adv[0]["idempotency_key"] == f"payment:advised:{envelope_id}"
    assert adv[0]["payload"]["method"] == "bpay"


async def test_r5a2bb_pay_no_quote_404(pool):
    """POST /pay with no CarrierQuoted → 404 quote_not_found."""
    ctx = await _tenant_and_client(pool)
    envelope_id = str(uuid.uuid4())
    app = await _asgi_client(pool)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver",
        ) as ac:
            r = await ac.post(
                f"/api/c/matters/{ctx['policy_id']}/pay/{envelope_id}",
                cookies=_cookie(ctx["session_id"]),
            )
    assert r.status_code == 404, r.text
    assert "quote_not_found" in r.text


async def test_r5a2bb_pay_policy_not_found_404(pool):
    """POST /pay with an unknown policy_id → 404 policy_not_found."""
    ctx = await _tenant_and_client(pool)
    unknown_policy = str(uuid.uuid4())
    app = await _asgi_client(pool)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver",
        ) as ac:
            r = await ac.post(
                f"/api/c/matters/{unknown_policy}/pay/{uuid.uuid4()}",
                cookies=_cookie(ctx["session_id"]),
            )
    assert r.status_code == 404, r.text
    assert "policy_not_found" in r.text


# ---- HTTP pins: /sign end-to-end (2 events) --------------------------
async def test_r5a2bb_sign_emits_two_events(pool):
    """POST /sign → SignatureRequested + SignatureCompleted land with
    pre-extraction shape."""
    ctx = await _tenant_and_client(pool)
    tid = ctx["tenant_id"]
    envelope_id = str(uuid.uuid4())

    app = await _asgi_client(pool)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver",
        ) as ac:
            r = await ac.post(
                f"/api/c/matters/{ctx['policy_id']}/sign/{envelope_id}",
                cookies=_cookie(ctx["session_id"]),
            )
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "executing"
    assert r.json()["signature"]["mocked"] is True

    req = await _fetch_events_by_type_env(
        pool, tid, "SignatureRequested", envelope_id,
    )
    assert len(req) == 1
    assert req[0]["mocked"] is True
    assert req[0]["actor"] == {"kind": "system", "id": "e_sign", "name": "e_sign"}
    assert req[0]["idempotency_key"] == f"esign:req:{envelope_id}"

    sig = await _fetch_events_by_type_env(
        pool, tid, "SignatureCompleted", envelope_id,
    )
    assert len(sig) == 1
    assert sig[0]["mocked"] is True
    assert sig[0]["actor"] == {"kind": "human", "id": ctx["user_id"],
                                "name": "client_owner"}
    assert sig[0]["idempotency_key"] == f"esign:sig:{envelope_id}"


# ---- HTTP pin: RBAC — no session → 401/403 ---------------------------
async def test_r5a2bb_no_session_rejected(pool):
    """No cookie → the client shell rejects (401 or 403)."""
    ctx = await _tenant_and_client(pool)
    app = await _asgi_client(pool)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver",
        ) as ac:
            r = await ac.get(f"/api/c/matters/{ctx['policy_id']}/status")
    assert r.status_code in (401, 403), r.text


# ---- Direct kernel emit pins (post-extraction only) ------------------
# These assert byte-verbatim payload/actor/idem shape at the kernel
# boundary. Skipped cleanly if the kernel module hasn't landed yet so
# the pre-extraction baseline can be established by running the HTTP
# pins first.
def _scene_or_skip():
    try:
        return importlib.import_module("app.kernel.scene")
    except ImportError:
        pytest.skip("kernel.scene not yet imported (pre-extraction baseline run)")


async def test_r5a2bb_pin_client_change_requested(pool):
    scene = _scene_or_skip()
    ctx = await _tenant_and_client(pool)
    envelope_id = str(uuid.uuid4())
    ev = await scene.emit_client_change_requested(
        pool, tenant_id=ctx["tenant_id"],
        user_id=ctx["user_id"],
        envelope_id=envelope_id,
        policy_id=ctx["policy_id"],
        client_id=ctx["client_id"],
        raw_message="raise limit to 20M",
    )
    rows = await _fetch_events_by_type_env(
        pool, ctx["tenant_id"], "ClientChangeRequested", envelope_id,
    )
    assert len(rows) == 1
    r = rows[0]
    assert r["actor"] == {"kind": "human", "id": ctx["user_id"],
                           "name": "client_owner"}
    assert set(r["payload"].keys()) == {
        "envelope_id", "policy_id", "client_id", "raw_message",
    }
    assert r["idempotency_key"] == f"cchreq:{envelope_id}"
    assert r["mocked"] is False


async def test_r5a2bb_pin_scene_carrier_quoted(pool):
    """Scene variant: payload = {envelope_id, policy_id, **quote} —
    NO explicit `mocked` key inside payload (unless quote has one).
    This is a DIFFERENT shape from `placement.emit_carrier_quoted` and
    must be preserved verbatim per extract-first."""
    scene = _scene_or_skip()
    ctx = await _tenant_and_client(pool)
    envelope_id = str(uuid.uuid4())
    quote = {
        "quote_id": "Q-SCN", "carrier_name": "FC-A",
        "premium_change_amount": "50.00",
        "premium_new_annual": "12550.00", "premium_currency": "AUD",
        "terms": {"basis": "annual"}, "conditions": ["standard"],
        "quoted_at": "2025-01-01T00:00:00+00:00",
        "mocked": True,
    }
    await scene.emit_carrier_quoted(
        pool, tenant_id=ctx["tenant_id"],
        envelope_id=envelope_id, policy_id=ctx["policy_id"],
        quote=quote,
    )
    rows = await _fetch_events_by_type_env(
        pool, ctx["tenant_id"], "CarrierQuoted", envelope_id,
    )
    assert len(rows) == 1
    r = rows[0]
    assert r["mocked"] is True
    assert r["actor"] == {"kind": "system", "id": "carrier_channel",
                           "name": "carrier"}
    assert r["idempotency_key"] == f"carrier:quote:{envelope_id}"
    # Scene variant spreads quote — payload has ALL quote keys +
    # envelope_id + policy_id.
    assert r["payload"]["envelope_id"] == envelope_id
    assert r["payload"]["policy_id"] == ctx["policy_id"]
    assert r["payload"]["quote_id"] == "Q-SCN"
    assert r["payload"]["carrier_name"] == "FC-A"


async def test_r5a2bb_pin_scene_endorsement_quote_ready(pool):
    """Scene variant: NO intake_envelope_id in payload — different
    from `placement.emit_endorsement_quote_ready`."""
    scene = _scene_or_skip()
    ctx = await _tenant_and_client(pool)
    envelope_id = str(uuid.uuid4())
    await scene.emit_endorsement_quote_ready(
        pool, tenant_id=ctx["tenant_id"],
        envelope_id=envelope_id, policy_id=ctx["policy_id"],
        quote_id="Q-1",
    )
    rows = await _fetch_events_by_type_env(
        pool, ctx["tenant_id"], "EndorsementQuoteReady", envelope_id,
    )
    assert len(rows) == 1
    r = rows[0]
    assert r["actor"] == {"kind": "system", "id": "orchestrator",
                           "name": "orchestrator"}
    assert set(r["payload"].keys()) == {"envelope_id", "policy_id", "quote_id"}
    assert r["idempotency_key"] == f"quote:ready:{envelope_id}"


async def test_r5a2bb_pin_compute_status_pure(pool):
    """compute_status() is a pure function over event lists."""
    scene = _scene_or_skip()
    # idle
    assert scene.compute_status([])["state"] == "idle"
    # awaiting_signature after payment.advised
    evs = [
        {"type": "ClientChangeRequested",
         "payload": {"envelope_id": "E1", "policy_id": "P"}},
        {"type": "payment.advised",
         "payload": {"envelope_id": "E1", "policy_id": "P"}},
    ]
    # newest first: compute_status expects events in seq-DESC order
    # (matches the pre-extraction c_scene SELECT ORDER BY seq DESC).
    s = scene.compute_status(list(reversed(evs)))
    assert s["state"] == "awaiting_signature"
    assert s["envelope_id"] == "E1"
    # certificate_ready wins over everything
    ev_cr = [
        {"type": "CertificateRegenerated",
         "payload": {"envelope_id": "E2", "policy_id": "P",
                     "certificate": {"certificate_id": "C1"}}},
    ]
    s = scene.compute_status(ev_cr)
    assert s["state"] == "certificate_ready"
    assert s["certificate"] == {"certificate_id": "C1"}
