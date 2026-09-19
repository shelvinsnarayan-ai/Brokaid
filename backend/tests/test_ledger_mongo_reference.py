"""R5c-4a — ledger parity → Mongo-reference (placeholder).

PG side removed; Mongo-side assertions covered by
`test_r5c1a_brain_dispatch.py::pin 3` (byte-identical hash chain
across backends with matched overrides) and
`test_r5c3_scripts_bundle_mongo.py` (pre-signed bundle verify).
"""
import pytest

pytestmark = pytest.mark.skip(
    reason="R5c-4a: Mongo assertions covered by R5c-1a pin 3 "
           "+ R5c-3b bundle pins; parity file kept as placeholder."
)


def test_ledger_mongo_reference_placeholder():
    pass
