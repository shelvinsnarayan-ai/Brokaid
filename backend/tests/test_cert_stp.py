"""R0.4 — R-CoC-01 certificate STP gate: 5 pass cases (200 path) + 5 fail cases (202 path)."""
import pytest

from app.kernel.cert_stp import PLAIN, evaluate

OK = {"status": "managed", "premium_status": "current"}


@pytest.mark.parametrize("policy,facts,special", [
    (OK, {"recent_limit_change": False, "open_claim": False}, None),
    ({"status": "in_force"}, {"recent_limit_change": False, "open_claim": False}, ""),
    (OK, {"recent_limit_change": False, "open_claim": False}, "Harbour Council noted as interested party"),
    (OK, {"recent_limit_change": False, "open_claim": False}, "Additional insured: Fictional Bank"),
    (OK, {"recent_limit_change": False, "open_claim": False}, "Loss payee: Fictional Finance Pty Ltd"),
])
def test_all_pass_is_stp(policy, facts, special):
    assert evaluate(policy, special=special, **facts) == []


@pytest.mark.parametrize("policy,facts,special,reason", [
    ({"status": "tracked"}, {"recent_limit_change": False, "open_claim": False}, None, "policy_not_in_force"),
    ({**OK, "premium_status": "overdue"}, {"recent_limit_change": False, "open_claim": False}, None, "premium_overdue"),
    (OK, {"recent_limit_change": True, "open_claim": False}, None, "limits_changed_recently"),
    (OK, {"recent_limit_change": False, "open_claim": True}, None, "open_claim"),
    (OK, {"recent_limit_change": False, "open_claim": False}, "Interested party, subject to custom override wording",
     "non_standard_wording"),
])
def test_each_failure_routes_to_broker(policy, facts, special, reason):
    reasons = evaluate(policy, special=special, **facts)
    assert reasons == [reason]
    assert PLAIN[reason]
