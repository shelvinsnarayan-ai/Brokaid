"""Coverage-language guard: 20 red-team + 20 benign + determinism."""
from __future__ import annotations

import pytest
from hypothesis import given, settings, strategies as st

from app.kernel.gate.coverage_guard import check


# ---- Red-team: must be BLOCKED ---------------------------------------------

RED = [
    "You are covered under this policy.",
    "You're covered for this claim.",
    "Coverage applies to your loss.",
    "We'll pay the claim.",
    "We will pay you the full amount.",
    "The insurer will pay this out.",
    "The claim will be paid by end of week.",
    "This is covered.",
    "It's covered — proceed.",
    "Not covered under section 4.",
    "No coverage for that event.",
    "Coverage is denied.",
    "Your claim is declined.",
    "The claim has been declined.",
    "Policy responds to that incident.",
    "The policy does not respond in this case.",
    "Excluded under clause 2.7.",
    "This falls within the exclusion.",
    "The insurer approves the claim.",
    "Your policy covers this incident.",
]

BENIGN = [
    "I've asked the insurer to confirm coverage.",
    "Your broker will confirm whether this is covered.",
    "Please provide additional documentation.",
    "We received your claim and will investigate.",
    "The assessor has been booked for next Tuesday.",
    "You can log into the portal to see your claim status.",
    "We have declined the quote from insurer B — not related to your claim.",
    "The recovered vehicle is at the workshop.",
    "Coverage confirmation is pending underwriter review.",
    "Please read the policy wording carefully.",
    "Your adviser will explain the exclusions to you.",
    "We are gathering information to submit to the insurer.",
    "The excess quoted is $1,500.",
    "Please note that repairs may take up to 6 weeks.",
    "I have logged your complaint in our register.",
    "Thanks for uploading the invoices.",
    "Kindly confirm your preferred contact method.",
    "The insurer has requested a photograph of the damage.",
    "We will update you within 5 business days.",
    "Please expect a call from the assessor tomorrow.",
]


@pytest.mark.parametrize("text", RED)
def test_red_team_blocked(text):
    r = check(text)
    assert r["passed"] is False, (
        f"red-team not blocked: {text!r}"
    )
    assert r["blocked_spans"], text


@pytest.mark.parametrize("text", BENIGN)
def test_benign_passes(text):
    r = check(text)
    assert r["passed"] is True, (
        f"benign wrongly blocked: {text!r} — hits: {r['blocked_spans']}"
    )


# ---- Determinism -----------------------------------------------------------

@settings(max_examples=100, deadline=None)
@given(text=st.text(min_size=0, max_size=200))
def test_deterministic(text):
    a = check(text)
    b = check(text)
    assert a == b
