"""R5c-4a — identity parity → Mongo-reference.

Was `test_identity_parity.py`. PG side removed; retains as a
placeholder module — R5c-4b/c will restore Mongo-side assertions
against `kernel/identity_mongo`.
"""
import pytest

pytestmark = pytest.mark.skip(
    reason="R5c-4a: PG side stripped; Mongo reference assertions "
           "pending R5c-4b/c migration."
)


def test_identity_mongo_reference_placeholder():
    pass
