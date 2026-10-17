"""R7-Demo-Seed — burmac-only data hardening for SIRC readability.

Runs *after* the existing burmac seed chain. Does NOT touch
`seed_ci_deterministic_mongo` — the ci-alpha / ci-beta bundle chain
root `92e61063…` must not drift.

Adds, idempotently, under the burmac tenant only:

  1. Three additional AU-plausible fictional clients so the operator
     Client 360 has a real book to browse.
  2. Two additional managed policies for those clients so the KPI-1
     Book number moves off its single-client value.
  3. Five additional queue cards spanning three class keys, three
     SLA bands (comfortable / near / overdue), and each carrying a
     citations array and a matter-subject line in the proposal.
  4. Two `PolicyRenewed` events so KPI-3 (retention rate) stops
     reading "—".
  5. Three `ClaimTimeframeMet` events plus a `ClaimClosed` so KPI-7
     (claims timeframe compliance) computes.
  6. A live certificate regeneration for Kai's Marine Liability
     policy so `_system_verifier_tokens` holds a live entry and the
     client-side QR resolves end-to-end. This closes the
     V1 Review Pack §5 / §10 High-severity flag.
  7. A pending client-approval envelope on Kai's Marine Liability
     policy so ClientHome can render a real "next action" card.

Idempotent via a SeedMarker with key `seed:burmac_extras:v1`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.db.tenant_safe import wrap
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch
from app.kernel.trail_mongo import DuplicateIdempotencyKey
from app.kernel import workflow


EXTRAS_KEY = "seed:burmac_extras:v1"

# Deterministic UUIDs (seeded, not random) so re-runs are idempotent.
EXTRA_CLIENTS = [
    {
        "client_id": "00000000-0000-4000-8000-000000000010",
        "display_name": "Bayside Bakery Pty Ltd",
        "industry": "food_manufacturing",
        "domicile": "Adelaide, AU",
        "jurisdiction_code": "AU",
    },
    {
        "client_id": "00000000-0000-4000-8000-000000000011",
        "display_name": "North Coast Freight Pty Ltd",
        "industry": "road_transport",
        "domicile": "Coffs Harbour, AU",
        "jurisdiction_code": "AU",
    },
    {
        "client_id": "00000000-0000-4000-8000-000000000012",
        "display_name": "Hillcrest Landscaping Pty Ltd",
        "industry": "landscape_services",
        "domicile": "Brisbane, AU",
        "jurisdiction_code": "AU",
    },
]

EXTRA_POLICIES = [
    {
        "policy_id": "00000000-0000-4000-8000-000000000110",
        "client_id": "00000000-0000-4000-8000-000000000010",
        "product": "Business Pack",
        "insurer_display": "Southern Cross Mutual (fictional)",
        "premium_amount": 4_250.00,
    },
    {
        "policy_id": "00000000-0000-4000-8000-000000000111",
        "client_id": "00000000-0000-4000-8000-000000000011",
        "product": "Motor Fleet",
        "insurer_display": "Great Dividing Insurance (fictional)",
        "premium_amount": 18_900.00,
    },
]

KAIS_MARINE_POLICY_ID = "00000000-0000-4000-8000-000000000101"
KAIS_CLIENT_ID = "00000000-0000-4000-8000-000000000001"

# Five queue cards spanning class + SLA + evidence variety. Deterministic
# envelope_ids so the frontend deep-link tests can pin them.
EXTRA_QUEUE_CARDS = [
    {
        "envelope_id": "0a0a0a0a-0001-4000-8000-000000000001",
        "class_key": "submit_to_market",
        "gate_reason": "non_standard_wording",
        "confidence": 0.842,
        "aged_seconds": 3 * 3600,           # 3h → warn band
        "routed_to_role": "principal",
        "client_id": "00000000-0000-4000-8000-000000000011",
        "matter_subject": "North Coast Freight — Motor Fleet renewal submission",
        "citations": [
            {"kind": "policy", "policy_id": "00000000-0000-4000-8000-000000000111",
             "excerpt": "current motor fleet schedule, 12 vehicles"},
            {"kind": "email", "excerpt": "client requested +2 vehicles by 30 Nov"},
        ],
    },
    {
        "envelope_id": "0a0a0a0a-0002-4000-8000-000000000002",
        "class_key": "endorsement_execute_instructed_quoted",
        "gate_reason": "premium_shift_gt_10pct",
        "confidence": 0.771,
        "aged_seconds": 26 * 3600,          # 26h → overdue
        "routed_to_role": "principal",
        "client_id": "00000000-0000-4000-8000-000000000010",
        "matter_subject": "Bayside Bakery — cold-storage extension endorsement",
        "citations": [
            {"kind": "policy", "policy_id": "00000000-0000-4000-8000-000000000110",
             "excerpt": "Business Pack — Property section"},
            {"kind": "quote", "excerpt": "Southern Cross Mutual (mocked) revised premium AUD 4,860"},
        ],
    },
    {
        "envelope_id": "0a0a0a0a-0003-4000-8000-000000000003",
        "class_key": "structure_risk",
        "gate_reason": "sum_insured_delta_gt_25pct",
        "confidence": 0.688,
        "aged_seconds": 40 * 60,            # 40 min → comfortable
        "routed_to_role": "principal",
        "client_id": "00000000-0000-4000-8000-000000000012",
        "matter_subject": "Hillcrest Landscaping — new commercial premises risk survey",
        "citations": [
            {"kind": "survey", "excerpt": "site inspection 12 Sep — sprinkler grade B"},
        ],
    },
    {
        "envelope_id": "0a0a0a0a-0004-4000-8000-000000000004",
        "class_key": "claim_admin",
        "gate_reason": "coverage_language_guard",
        "confidence": 0.803,
        "aged_seconds": 6 * 3600,           # 6h → warn
        "routed_to_role": "accounts",
        "client_id": "00000000-0000-4000-8000-000000000003",  # Kelp
        "matter_subject": "Kelp Shipping — cargo claim status update to client",
        "citations": [
            {"kind": "claim", "claim_number": "CLM-2026-000001"},
            {"kind": "email", "excerpt": "client asked for ETA on liability decision"},
        ],
    },
    {
        "envelope_id": "0a0a0a0a-0005-4000-8000-000000000005",
        "class_key": "comms_service",
        "gate_reason": "compliance_check_pending",
        "confidence": 0.914,
        "aged_seconds": 15 * 60,            # 15 min → comfortable
        "routed_to_role": "compliance",
        "client_id": KAIS_CLIENT_ID,
        "matter_subject": "Harbour Marine — renewal reminder outbound",
        "citations": [
            {"kind": "policy", "policy_id": KAIS_MARINE_POLICY_ID,
             "excerpt": "expiry 2026-11-30, first reminder 2026-10-15"},
        ],
    },
]

# Client-facing pending envelope Kai will see on the home tab.
KAI_PENDING_ENDORSEMENT_ID = "0b0b0b0b-0001-4000-8000-000000000001"


async def _already_seeded(db, tenant_id: str) -> bool:
    safe = wrap(db)
    async with use_tenant(tenant_id):
        row = await safe.events.find_one({"idempotency_key": EXTRAS_KEY})
    return bool(row)


async def _seed_clients(db, tenant_id: str) -> None:
    safe = wrap(db)
    for c in EXTRA_CLIENTS:
        async with use_tenant(tenant_id):
            existing = await safe.clients.find_one({"client_id": c["client_id"]})
            if not existing:
                try:
                    await safe.clients.insert_one(dict(c))
                except (DuplicateKeyError, DuplicateIdempotencyKey):
                    pass
        try:
            await append_via_dispatch(
                db, tenant_id, "ClientEntityCreated",
                actor={"kind": "system", "id": "seed", "name": "burmac-extras-seed"},
                payload={**c, "fictional": True},
                idempotency_key=f"seed:burmac_extras:client:{c['client_id']}",
            )
        except (DuplicateKeyError, DuplicateIdempotencyKey):
            pass


async def _seed_policies(db, tenant_id: str) -> None:
    safe = wrap(db)
    now = datetime.now(timezone.utc)
    for p in EXTRA_POLICIES:
        doc = {
            "policy_id": p["policy_id"],
            "client_id": p["client_id"],
            "status": "managed",
            "product": p["product"],
            "insurer_display": p["insurer_display"],
            "inception": now - timedelta(days=90),
            "expiry": now + timedelta(days=275),
            "premium_amount": p["premium_amount"],
            "premium_currency": "AUD",
            "schedule": {"cover_types": [], "excess": None,
                         "territorial_limits": "Australia"},
            "imported_from": None,
        }
        async with use_tenant(tenant_id):
            existing = await safe.policies.find_one({"policy_id": p["policy_id"]})
            if not existing:
                try:
                    await safe.policies.insert_one(doc)
                except (DuplicateKeyError, DuplicateIdempotencyKey):
                    pass
        try:
            await append_via_dispatch(
                db, tenant_id, "PolicyCreated",
                actor={"kind": "system", "id": "seed", "name": "burmac-extras-seed"},
                payload={**doc, "inception": doc["inception"].isoformat(),
                         "expiry": doc["expiry"].isoformat(),
                         "mocked": True, "fictional": True},
                idempotency_key=f"seed:burmac_extras:policy:{p['policy_id']}",
            )
        except (DuplicateKeyError, DuplicateIdempotencyKey):
            pass


async def _seed_queue_cards(db, tenant_id: str) -> None:
    safe = wrap(db)
    now = datetime.now(timezone.utc)
    for spec in EXTRA_QUEUE_CARDS:
        env_id = spec["envelope_id"]
        hitl_since = now - timedelta(seconds=spec["aged_seconds"])
        proposal_key = f"seed:burmac_extras:proposal:{env_id}"
        proposal_ref: str
        try:
            proposal = await append_via_dispatch(
                db, tenant_id, "AgentOutputProduced",
                actor={"kind": "system", "id": "agent", "name": "agent"},
                payload={
                    "envelope_id": env_id,
                    "class_key": spec["class_key"],
                    "output": {
                        "summary": spec["matter_subject"],
                        "fields": {"mocked_capabilities": ["carrier"]},
                    },
                    "confidence": {
                        "components": {
                            "schema": spec["confidence"] + 0.02,
                            "completeness": spec["confidence"] + 0.01,
                            "citations": spec["confidence"] + 0.04,
                            "class": spec["confidence"] - 0.02,
                        },
                        "scalar": spec["confidence"],
                    },
                    "citations": spec["citations"],
                    "tokens_used": {"input": 0, "output": 0},
                    "mocked_capabilities": ["carrier"],
                },
                idempotency_key=proposal_key,
                mocked=True,
            )
            proposal_ref = proposal["event_id"]
        except (DuplicateKeyError, DuplicateIdempotencyKey):
            async with use_tenant(tenant_id):
                row = await safe.events.find_one({"idempotency_key": proposal_key})
            assert row is not None
            proposal_ref = row["event_id"]

        try:
            await workflow.enqueue(
                db, tenant_id=tenant_id, envelope_id=env_id,
                class_key=spec["class_key"], proposal_ref=proposal_ref,
                matter_id=None, client_id=spec["client_id"],
                gate_reason=spec["gate_reason"],
                confidence=spec["confidence"],
                routed_to_role=spec["routed_to_role"],
                hitl_since=hitl_since,
            )
        except (DuplicateKeyError, DuplicateIdempotencyKey):
            pass


async def _seed_renewals(db, tenant_id: str) -> None:
    now = datetime.now(timezone.utc)
    for i, pol in enumerate([
        {"policy_id": KAIS_MARINE_POLICY_ID, "premium": 8_500.00},
        {"policy_id": "00000000-0000-4000-8000-000000000103",
         "premium": 12_400.00},                          # Kelp
    ]):
        try:
            await append_via_dispatch(
                db, tenant_id, "PolicyRenewed",
                actor={"kind": "system", "id": "seed",
                       "name": "burmac-extras-seed"},
                payload={
                    "policy_id": pol["policy_id"],
                    "kind": "renewed",
                    "value": pol["premium"],
                    "renewed_at": (now - timedelta(days=30 * (i + 1))).isoformat(),
                    "fictional": True,
                },
                idempotency_key=f"seed:burmac_extras:renewed:{pol['policy_id']}",
            )
        except (DuplicateKeyError, DuplicateIdempotencyKey):
            pass


async def _seed_claim_timeframes(db, tenant_id: str) -> None:
    now = datetime.now(timezone.utc)
    for i in range(3):
        env_id = f"0c0c0c0c-000{i + 1}-4000-8000-000000000{i:03d}"
        try:
            await append_via_dispatch(
                db, tenant_id, "ClaimTimeframeMet",
                actor={"kind": "system", "id": "seed",
                       "name": "burmac-extras-seed"},
                payload={
                    "envelope_id": env_id,
                    "claim_number": f"CLM-2026-00000{i + 2}",
                    "kind": "acknowledged_within_10_business_days",
                    "closed_at": (now - timedelta(days=i * 2)).isoformat(),
                    "fictional": True,
                },
                idempotency_key=f"seed:burmac_extras:timeframe:{env_id}",
            )
        except (DuplicateKeyError, DuplicateIdempotencyKey):
            pass


async def _seed_kai_pending_endorsement(db, tenant_id: str) -> None:
    """Emit an AgentOutputProduced for a client-visible pending endorsement,
    which the /api/c/next-action route picks up. Not enqueued on the operator
    queue — this envelope is client-facing."""
    now = datetime.now(timezone.utc)
    try:
        await append_via_dispatch(
            db, tenant_id, "AgentOutputProduced",
            actor={"kind": "system", "id": "agent", "name": "agent"},
            payload={
                "envelope_id": KAI_PENDING_ENDORSEMENT_ID,
                "class_key": "endorsement_awaiting_client_approval",
                "policy_id": KAIS_MARINE_POLICY_ID,
                "client_id": KAIS_CLIENT_ID,
                "output": {
                    "summary": (
                        "Harbour Marine — quote ready for your approval. "
                        "Adds Public Liability cover for the new Sydney "
                        "warehouse operation."
                    ),
                    "fields": {"revised_premium_aud": 9_150.00,
                               "delta_aud": 650.00},
                },
                "confidence": {"scalar": 0.91},
                "citations": [
                    {"kind": "policy", "policy_id": KAIS_MARINE_POLICY_ID,
                     "excerpt": "Marine Liability — endorsement 2026-11 draft"},
                ],
                "tokens_used": {"input": 0, "output": 0},
                "mocked_capabilities": ["carrier"],
                "raised_at": now.isoformat(),
            },
            idempotency_key=(
                "seed:burmac_extras:pending:"
                f"{KAI_PENDING_ENDORSEMENT_ID}"
            ),
            mocked=True,
        )
    except (DuplicateKeyError, DuplicateIdempotencyKey):
        pass


async def _seed_kai_verifier_token(db, tenant_id: str) -> None:
    """Run a live certificate-regenerate cycle for Kai's Marine Liability
    so `verifier_tokens` has a real entry with a raw token that the
    `/verify/:token` route can resolve.

    Emits CertificateRegenerated + VerifierTokenIssued events into the
    ledger under the burmac tenant. Idempotent via the outer SeedMarker
    guard — the certificate itself gets a fresh id / token per run but we
    only run once."""
    from app.kernel.certificate_mongo import regenerate_certificate
    try:
        await regenerate_certificate(
            store=db, tenant_id=tenant_id,
            policy_id=KAIS_MARINE_POLICY_ID,
            envelope_id=KAI_PENDING_ENDORSEMENT_ID,
        )
    except Exception as e:                                  # pragma: no cover
        # Log but don't halt the seed — the review pack §5 flag reopens.
        print(f"seed_burmac_extras: certificate regenerate skipped: {e}")


# Demo verifier tokens for R1 #3 / SIRC — stable, deterministic strings
# so demo bookmarks survive re-seeds. Length >= 20 satisfies the
# verifier route's format guard.
DEMO_TOKENS = [
    {
        "raw": "BURMAC-DEMO-VALID-2026-CERTIFICATE-KAI-MARINE",
        "certificate_id": "d0d0d0d0-c001-4000-8000-000000000001",
        "verdict": "VALID",
        "expires_days": 90, "revoked": False, "tampered": False,
        "holder_name": "Sydney Harbour Marina Pty Ltd",
        "holder_address": "12 Wharf Road, Rozelle NSW 2039, Australia",
        "holder_email": "operations@sydneyharbourmarina.example",
        "interest_type": "additional_insured",
        "special": (
            "Marina requires additional insured status on Public Liability "
            "for wet-berth operations for the 2026-27 season."
        ),
    },
    {
        "raw": "BURMAC-DEMO-TAMPERED-2026-CERTIFICATE-KAI-MARINE",
        "certificate_id": "d0d0d0d0-c002-4000-8000-000000000002",
        "verdict": "TAMPERED",
        "expires_days": 90, "revoked": False, "tampered": True,
        "holder_name": "Sydney Harbour Marina Pty Ltd",
        "holder_address": "12 Wharf Road, Rozelle NSW 2039, Australia",
        "holder_email": "operations@sydneyharbourmarina.example",
        "interest_type": "additional_insured",
        "special": (
            "Payload signature invalid — this certificate should not be "
            "accepted. Contact Harbourline to reissue."
        ),
    },
    {
        "raw": "BURMAC-DEMO-EXPIRED-2025-CERTIFICATE-KAI-MARINE",
        "certificate_id": "d0d0d0d0-c003-4000-8000-000000000003",
        "verdict": "EXPIRED",
        "expires_days": -30, "revoked": False, "tampered": False,
        "holder_name": "Riverside Boatyard Pty Ltd",
        "holder_address": "3 Blackwattle Bay, Glebe NSW 2037, Australia",
        "holder_email": "manager@riversideboatyard.example",
        "interest_type": "interested_party",
        "special": "Historic certificate — 2025-26 season.",
    },
    {
        "raw": "BURMAC-DEMO-REVOKED-2026-CERTIFICATE-KAI-MARINE",
        "certificate_id": "d0d0d0d0-c004-4000-8000-000000000004",
        "verdict": "REVOKED",
        "expires_days": 90, "revoked": True, "tampered": False,
        "holder_name": "North Head Ferries Pty Ltd",
        "holder_address": "1 Manly Cove, Manly NSW 2095, Australia",
        "holder_email": "compliance@northheadferries.example",
        "interest_type": "loss_payee",
        "special": (
            "Withdrawn after cover terms were renegotiated. Reissue "
            "expected on request."
        ),
    },
]

DEMO_TOKENS_MARKER = "seed:burmac_extras_demo_verifier_tokens:v1"


async def _seed_demo_verifier_tokens(db, tenant_id: str) -> None:
    """Seed VALID / TAMPERED / EXPIRED / REVOKED verifier tokens for
    the SIRC demo. Idempotent: verifier_tokens has a unique index on
    token_hash so re-runs no-op; CertificateRegenerated events are
    idempotent via idempotency_key."""
    from app.kernel.certificate_mongo import hash_token
    safe = wrap(db)
    now = datetime.now(timezone.utc)

    for spec in DEMO_TOKENS:
        raw = spec["raw"]
        token_hash = hash_token(raw)
        cert_id = spec["certificate_id"]
        expires = now + timedelta(days=spec["expires_days"])
        revoked_at = now - timedelta(days=1) if spec["revoked"] else None
        interest = spec["interest_type"]
        holder_block = {
            "name": spec["holder_name"],
            "address": spec["holder_address"],
            "email": spec["holder_email"],
        }
        req_block = {
            "interest_type": interest,
            "effective_from": now.isoformat(timespec="microseconds"),
            "effective_to": (now + timedelta(days=365)).isoformat(timespec="microseconds"),
            "special_requirements": spec["special"],
        }
        certificate = {
            "certificate_id": cert_id,
            "policy_id": KAIS_MARINE_POLICY_ID,
            "holder_display": spec["holder_name"],
            "holder": holder_block,
            "requirements": req_block,
            "insurer_display": "Fictional Carrier A",
            "product": "Marine Liability",
            "cover_types": [
                {"type": "Public Liability", "limit_any_one": "20,000,000",
                 "currency": "AUD"},
                {"type": "Products Liability", "limit_any_one": "20,000,000",
                 "currency": "AUD"},
            ],
            "excess": "5,000 AUD",
            "territorial_limits": "Australia",
            "effective_from": req_block["effective_from"],
            "effective_to": req_block["effective_to"],
            "issued_at": now.isoformat(timespec="microseconds"),
            "mocked_insurer": True,
        }

        # 1) Emit CertificateRegenerated (idempotent).
        try:
            await append_via_dispatch(
                db, tenant_id, "CertificateRegenerated",
                actor={"kind": "system", "id": "certificate", "name": "certificate"},
                payload={
                    "envelope_id": None,
                    "policy_id": KAIS_MARINE_POLICY_ID,
                    "certificate_id": cert_id,
                    "qr_token_hash": token_hash,
                    "effective_date": now.isoformat(timespec="microseconds"),
                    "certificate": certificate,
                    "holder": holder_block,
                    "requirements": req_block,
                    "demo_verdict": spec["verdict"],
                    "mocked": True,
                },
                idempotency_key=f"seed:burmac_extras:demo_cert:{cert_id}",
                mocked=True,
            )
        except (DuplicateKeyError, DuplicateIdempotencyKey):
            pass

        # 2) Upsert verifier_tokens row with tampered_flag / expiry /
        #    revoked_at flavour.
        async with use_tenant(tenant_id):
            existing = await safe.verifier_tokens.find_one({"token_hash": token_hash})
            doc = {
                "token_hash": token_hash, "tenant_id": tenant_id,
                "policy_id": KAIS_MARINE_POLICY_ID,
                "certificate_id": cert_id,
                "issued_at": now, "expires_at": expires,
                "revoked_at": revoked_at, "rate_limit_count": 0,
                "tampered_flag": spec["tampered"],
                "holder": holder_block, "requirements": req_block,
                "demo_verdict": spec["verdict"],
            }
            if existing is None:
                try:
                    await safe.verifier_tokens.insert_one(doc)
                except (DuplicateKeyError, DuplicateIdempotencyKey):
                    pass
            else:
                await safe.verifier_tokens.update_one(
                    {"token_hash": token_hash},
                    {"$set": {
                        "expires_at": expires, "revoked_at": revoked_at,
                        "tampered_flag": spec["tampered"],
                        "holder": holder_block, "requirements": req_block,
                        "demo_verdict": spec["verdict"],
                    }},
                )


async def seed_burmac_extras(
    db: AsyncIOMotorDatabase, tenant_id: str
) -> dict:
    already = await _already_seeded(db, tenant_id)

    # Demo verifier tokens (R1 #3) run every boot: they are guarded by
    # the verifier_tokens unique index + append_via_dispatch idempotency
    # keys, so re-runs are cheap and existing DBs pick them up without
    # a marker bump on the outer v1 seed.
    await _seed_demo_verifier_tokens(db, tenant_id)

    if already:
        return {"seeded": False, "reason": "already_seeded",
                "demo_verifier_tokens": True}

    await _seed_clients(db, tenant_id)
    await _seed_policies(db, tenant_id)
    await _seed_queue_cards(db, tenant_id)
    await _seed_renewals(db, tenant_id)
    await _seed_claim_timeframes(db, tenant_id)
    await _seed_kai_verifier_token(db, tenant_id)

    try:
        await append_via_dispatch(
            db, tenant_id, "SeedMarker",
            actor={"kind": "system", "id": "seed", "name": "burmac-extras-seed"},
            payload={"kind": "burmac_extras", "version": "v1"},
            idempotency_key=EXTRAS_KEY,
        )
    except (DuplicateKeyError, DuplicateIdempotencyKey):
        pass
    return {"seeded": True, "demo_verifier_tokens": True}
