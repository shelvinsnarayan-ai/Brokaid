"""R0.11 follow-up — vehicle op normalisation, broker_reviewed status, review-approval alert."""
from decimal import Decimal

import pytest

from app.kernel import scene
from app.kernel.mocks.carrier_channel import vehicle_change_premium
from app.kernel.policy_field_guard import UnmatchedPolicyField, normalize_vehicle_ops, validate_requested_changes

HILUX = {"schedule": {"vehicles": [{"rego": "ABC-123", "vin": "MOCKVIN0000000ABC1"}, {"rego": "DEF-456"}]}}


def _el(path, op, to=None, frm=None):
    return {"field_path": path, "operation": op, "to_value": to, "from_value": frm,
            "citation": {"kind": "policy_schedule", "field_path": path}}


def test_element_path_swap_folds_to_list_ops_and_quotes_flat_fee():
    tester_shape = [
        _el("schedule.vehicles[ABC-123].registration", "set", to="XYZ-789", frm="ABC-123"),
        _el("schedule.vehicles[XYZ-789].registration", "set", to="XYZ-789"),
        _el("schedule.vehicles[XYZ-789].make_model_year", "set", to="2025 Ford Ranger XLT"),
        _el("schedule.vehicles[XYZ-789].use", "set", to="business"),
    ]
    out = normalize_vehicle_ops(HILUX, tester_shape)
    assert [(c["field_path"], c["operation"]) for c in out] == [("schedule.vehicles[]", "remove"), ("schedule.vehicles[]", "add")]
    assert out[1]["to_value"]["rego"] == "XYZ-789" and out[1]["to_value"]["use"] == "business"
    validate_requested_changes(HILUX, out)  # no park
    assert vehicle_change_premium(Decimal("2400"), 2, out) == Decimal("85.00")


def test_remove_op_plus_new_element_paths_fold():
    out = normalize_vehicle_ops(HILUX, [_el("schedule.vehicles[ABC-123].registration", "remove"),
                                        _el("schedule.vehicles[XYZ-789].vin", "set", to="VIN9")])
    assert [c["operation"] for c in out] == ["remove", "add"] and out[1]["to_value"]["vin"] == "VIN9"


def test_lone_edit_to_unknown_vehicle_still_parks():
    ch = [_el("schedule.vehicles[ZZZ-999].garaging_address", "set", to="1 Fake St")]
    assert normalize_vehicle_ops(HILUX, ch) == ch
    with pytest.raises(UnmatchedPolicyField) as exc:
        validate_requested_changes(HILUX, ch)
    assert exc.value.as_payload()["attempted_value"] == "ZZZ-999"


def test_canonical_list_ops_untouched_and_held_edits_kept():
    canon = [_el("schedule.vehicles[]", "remove", frm={"rego": "ABC-123"}), _el("schedule.vehicles[]", "add", to={"rego": "XYZ-789"})]
    assert normalize_vehicle_ops(HILUX, canon) == canon
    edit = [_el("schedule.vehicles[ABC-123].use", "set", to="private")]
    assert normalize_vehicle_ops(HILUX, edit) == edit


def test_status_after_broker_takes_review():
    env = {"envelope_id": "e1", "policy_id": "p1"}
    evs = [{"type": "ClientChangeRequested", "payload": env}, {"type": "RequestNeedsHumanReview", "payload": env}]
    assert scene.compute_status(list(reversed(evs)))["state"] == "awaiting_broker_review"
    evs += [{"type": "OperatorApproval", "payload": {"envelope_id": "e1"}}, {"type": "Approval", "payload": {"envelope_id": "e1"}}]
    assert scene.compute_status(list(reversed(evs)))["state"] == "broker_reviewed"


def test_status_ignores_older_envelopes():
    old = {"envelope_id": "OLD", "policy_id": "p1"}
    new = {"envelope_id": "NEW", "policy_id": "p1"}
    evs = [{"type": "SignatureRequested", "payload": old}, {"type": "payment.advised", "payload": old},
           {"type": "ClientChangeRequested", "payload": new}, {"type": "EndorsementQuoteReady", "payload": new}]
    s = scene.compute_status(list(reversed(evs)))
    assert s["envelope_id"] == "NEW" and s["state"] == "awaiting_operator"


def test_quote_ready_alerts_staff_and_review_approval_is_not_client_approval():
    from app.kernel.notify import BROKER_REVIEWED, RULES
    assert RULES["EndorsementQuoteReady"]["to"] == ("staff", ("ae",))
    assert RULES["EndorsementQuoteReady"]["link"] == "/o/queue/{envelope_id}"
    assert BROKER_REVIEWED["kind"] == "broker_reviewed" and BROKER_REVIEWED["to"] == ("client", None)


def test_awaiting_client_approval_until_client_taps():
    env = {"envelope_id": "E", "policy_id": "p1"}
    evs = [{"type": "ClientChangeRequested", "payload": env}, {"type": "EndorsementQuoteReady", "payload": env},
           {"type": "OperatorApproval", "payload": {"envelope_id": "E"}}]
    assert scene.compute_status(list(reversed(evs)))["state"] == "awaiting_client_approval"
    evs.append({"type": "Approval", "payload": env})  # the client's own tap carries policy_id
    assert scene.compute_status(list(reversed(evs)))["state"] == "awaiting_payment"


def test_paid_needs_client_approval_not_broker_lineage():
    import os
    from pathlib import Path

    import httpx
    from dotenv import dotenv_values
    base = dotenv_values(Path(__file__).resolve().parents[2] / "frontend/.env").get("REACT_APP_BACKEND_URL")
    c = httpx.Client(base_url=base, timeout=30)
    assert c.post("/api/auth/dev/impersonate", json={"email": "owner@fictionalclient.example"}).status_code == 200
    r = c.post("/api/c/matters/00000000-0000-4000-8000-000000000104/paid/62836e0c-3573-413f-b392-704cfbb7cd76", json={})
    assert r.status_code == 409 and "client Approval" in r.text
    assert os.environ is not None
