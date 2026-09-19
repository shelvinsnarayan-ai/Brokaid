"""R5c-4a — KPIs parity → Mongo-reference (placeholder).

PG side removed; Mongo-side assertions covered by
`test_r5c3_jobs_pins.py::test_pin_r5c3_persist_daily_parity`
(dispatch-aware persist_daily shape parity).
"""
import pytest

pytestmark = pytest.mark.skip(
    reason="R5c-4a: Mongo assertions covered by R5c-3 persist_daily pin.",
)


def test_r5a2a_kpis_mongo_reference_placeholder():
    pass
