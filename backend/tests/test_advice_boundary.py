"""R0.3 — thin F/G/P/X classifier matrix (rules only)."""
import pytest

from app.kernel.advice_boundary import classify
from app.seeds.seed_kai_motor_mongo import SCHEDULE

MOTOR = {"policy_id": "p104", "schedule": SCHEDULE}
MARINE = {"policy_id": "p101", "schedule": {"cover_types": [
    {"type": "Public Liability", "limit_any_one": 10_000_000}]}}

SWAP = [{"field_path": "schedule.vehicles[]", "operation": "remove", "from_value": {"rego": "ABC123"}},
        {"field_path": "schedule.vehicles[]", "operation": "add", "to_value": {"rego": "XYZ789"}}]
UNKNOWN = [{"field_path": "schedule.vehicles[]", "operation": "remove", "from_value": {"rego": "ZZZ999"}}]
ADDR = [{"field_path": "schedule.addresses[]", "operation": "replace", "from_value": SCHEDULE["addresses"][0],
         "to_value": "9 New St, Fictional Bay NSW 2000"}]
DRV_FULL = [{"field_path": "schedule.drivers[]", "operation": "add",
             "to_value": {"name": "Jo Fictional", "licence_number": "MOCK10003"}}]
DRV_THIN = [{"field_path": "schedule.drivers[]", "operation": "add", "to_value": {"name": "Jo"}}]
DRV_REMOVE = [{"field_path": "schedule.drivers[]", "operation": "remove", "from_value": {"licence_number": "MOCK10002"}}]
LIMIT = [{"field_path": "schedule.limits.public_liability", "operation": "set", "to_value": 30_000_000}]

CASES = [
    # vehicle
    ("swap my Navara ABC123 for a Hilux XYZ789", SWAP, MOTOR, "F", "VEHICLE-F-01"),
    ("please remove the ute ZZZ999", UNKNOWN, MOTOR, "P", "VEHICLE-P-02"),
    ("should I add this Hilux to the fleet or cover it personally?", [], MOTOR, "P", "VEHICLE-P-01"),
    ("add a 2024 Hilux rego XYZ789 to the fleet", SWAP[1:], MOTOR, "F", "VEHICLE-F-01"),
    ("is it better to put the new van on this policy?", [], MOTOR, "P", "VEHICLE-P-01"),
    # address
    ("we moved the garage to 9 New St", ADDR, MOTOR, "F", "ADDRESS-F-01"),
    ("our address has changed to 9 New St", [], MOTOR, "F", "ADDRESS-F-01"),
    ("we are relocating, do we need to tell anyone?", [], MOTOR, "P", "ADDRESS-P-01"),
    ("update the garaged address please", ADDR, MOTOR, "F", "ADDRESS-F-01"),
    # driver
    ("add Jo Fictional licence MOCK10003 as a driver", DRV_FULL, MOTOR, "F", "DRIVER-F-01"),
    ("remove Sam as a driver", DRV_REMOVE, MOTOR, "F", "DRIVER-F-01"),
    ("should Jo be a named driver?", [], MOTOR, "P", "DRIVER-P-01"),
    ("add Jo as a driver", DRV_THIN, MOTOR, "G", "DRIVER-G-01"),
    ("is my son suitable as a driver on the ute?", [], MOTOR, "P", "DRIVER-P-01"),
    # limit
    ("lift public liability from $10M to $30M", LIMIT, MARINE, "P", "LIMIT-P-01"),
    ("increase my cover to $20M", [], MARINE, "P", "LIMIT-P-01"),
    ("can we lower the excess?", [], MOTOR, "P", "LIMIT-P-01"),
    ("change the sum insured on the Navara", [], MOTOR, "P", "LIMIT-P-01"),
    # certificate
    ("I need a certificate of currency for the council", [], MARINE, "G", "CERT-G-01"),
    ("send me proof of insurance for a tender", [], MARINE, "G", "CERT-G-01"),
    ("certificate for Harbour Council as interested party", [], MARINE, "G", "CERT-G-01"),
    ("need a CoC today", [], MOTOR, "G", "CERT-G-01"),
    # fall-through
    ("what's the weather like?", [], MARINE, "X", "UNCLASSIFIED-X-01"),
    ("", [], MARINE, "X", "UNCLASSIFIED-X-01"),
]


@pytest.mark.parametrize("msg,changes,policy,tier,rule", CASES)
def test_classify_matrix(msg, changes, policy, tier, rule):
    v = classify(msg, {"requested_changes": changes}, policy)
    assert (v["tier"], v["rule_id"]) == (tier, rule), v
    assert v["reason"]


def test_limit_beats_other_signals():
    v = classify("swap the ute and lift the limit", {"requested_changes": SWAP + LIMIT}, MOTOR)
    assert v["tier"] == "P"


def test_deterministic():
    a = classify("swap my Navara ABC123 for a Hilux XYZ789", {"requested_changes": SWAP}, MOTOR)
    assert all(classify("swap my Navara ABC123 for a Hilux XYZ789", {"requested_changes": SWAP}, MOTOR) == a
               for _ in range(5))
