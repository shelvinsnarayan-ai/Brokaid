"""R0.2 — vehicle pricing bounds + made-up-field guard."""
from decimal import Decimal

import pytest

from app.kernel.mocks.carrier_channel import (
    VEHICLE_FLOOR, VEHICLE_SWAP_ADMIN_FEE, submit_endorsement, vehicle_change_premium,
)
from app.kernel.policy_field_guard import UnmatchedPolicyField, validate_requested_changes
from app.seeds.seed_kai_motor_mongo import POLICY_ID, PREMIUM, SCHEDULE

MOTOR = {"policy_id": POLICY_ID, "premium_amount": PREMIUM, "premium_currency": "AUD",
         "insurer_display": "Fictional Carrier A", "schedule": SCHEDULE}
MARINE = {"policy_id": "p101", "schedule": {"cover_types": [
    {"type": "Public Liability", "limit_any_one": 10_000_000}]}}


def _op(op, value=None, frm=None):
    return {"field_path": "schedule.vehicles[]", "operation": op,
            "to_value": value, "from_value": frm}


@pytest.mark.parametrize("base", [Decimal("100"), Decimal("3800"), Decimal("250000")])
@pytest.mark.parametrize("count", [0, 1, 2, 10])
@pytest.mark.parametrize("ops", [
    ["add"], ["remove"], ["add", "add", "add"], ["remove"] * 5, ["add", "remove"], [],
])
def test_vehicle_premium_bounded(base, count, ops):
    amt = vehicle_change_premium(base, count, [_op(o) for o in ops])
    assert abs(amt) <= base * Decimal("1.0") or amt == VEHICLE_SWAP_ADMIN_FEE
    if ops.count("add") == ops.count("remove"):
        assert amt == VEHICLE_SWAP_ADMIN_FEE
    else:
        assert abs(amt) <= base * Decimal("0.5")
        assert abs(amt) >= min(VEHICLE_FLOOR, base * Decimal("0.5"))


def test_vehicle_floor_and_cap():
    assert vehicle_change_premium(Decimal("3800"), 20, [_op("add")]) == Decimal("150.00")
    assert vehicle_change_premium(Decimal("3800"), 1, [_op("add")] * 9) == Decimal("1900.00")
    assert vehicle_change_premium(Decimal("3800"), 2, [_op("remove")]) == Decimal("-152.00")


async def test_swap_quotes_flat_admin_fee():
    q = await submit_endorsement(envelope_id="e1", policy_current=MOTOR, submission={
        "structured_submission": {"requested_changes": [
            _op("remove", frm={"rego": "ABC123"}), _op("add", {"rego": "XYZ789"})]}})
    assert q["premium_change_amount"] == "85.00" and q["mocked"] is True
    assert q["premium_new_annual"] == "3885.00"


def test_guard_accepts_swap_of_held_vehicle():
    validate_requested_changes(MOTOR, [
        _op("remove", frm={"rego": "abc 123"}), _op("add", {"rego": "XYZ789"})])


def test_guard_rejects_unknown_rego():
    with pytest.raises(UnmatchedPolicyField) as ei:
        validate_requested_changes(MOTOR, [_op("remove", frm={"rego": "ABC999"})])
    assert ei.value.field_path == "schedule.vehicles[]"
    assert ei.value.attempted == {"rego": "ABC999"}


def test_guard_rejects_vehicle_on_marine_policy():
    with pytest.raises(UnmatchedPolicyField):
        validate_requested_changes(MARINE, [_op("add", {"rego": "XYZ789"})])


def test_guard_drivers_addresses_limits():
    validate_requested_changes(MOTOR, [{"field_path": "schedule.drivers[]", "operation": "remove",
                                        "from_value": {"licence_number": "MOCK10001"}}])
    with pytest.raises(UnmatchedPolicyField):
        validate_requested_changes(MOTOR, [{"field_path": "schedule.drivers[]", "operation": "remove",
                                            "from_value": {"name": "Nobody"}}])
    with pytest.raises(UnmatchedPolicyField):
        validate_requested_changes(MOTOR, [{"field_path": "schedule.vehicles[0].garaged_address",
                                            "operation": "set", "from_value": "1 Fake St",
                                            "to_value": "2 Fake St"}])
    validate_requested_changes(MARINE, [{"field_path": "schedule.limits.public_liability",
                                         "operation": "set", "to_value": 30_000_000}])
    with pytest.raises(UnmatchedPolicyField):
        validate_requested_changes(MARINE, [{"field_path": "schedule.limits.cyber",
                                             "operation": "set", "to_value": 1}])


def test_guard_accepts_registration_alias_and_text():
    validate_requested_changes(MOTOR, [_op("remove", frm={"registration": "ABC123"})])
    validate_requested_changes(MOTOR, [_op("remove", frm={"description": "Navara ABC123"})])
    with pytest.raises(UnmatchedPolicyField):
        validate_requested_changes(MOTOR, [_op("remove", frm={"description": "Nissan Navara"})])
