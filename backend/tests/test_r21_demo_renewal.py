"""R2.1: the Kai renewal demo reaches click-to-accept (never accepted) and is re-runnable via a ledgered reset."""
from __future__ import annotations

import pytest

from app.db.tenant_safe import wrap
from app.kernel import demo_renewal
from app.kernel.tenant_ctx import use_tenant


@pytest.mark.asyncio
async def test_kai_renewal_demo_stops_at_accept_and_reruns(seeded, app_client, mongo_db):
    app, _ = app_client
    tid = seeded["tenant_id"]
    first = await demo_renewal.prepare(app, mongo_db, tid, "demo-operator")
    assert first["stage"] == "paid" and first["insurer"] == demo_renewal.MANUAL["insurer"]
    assert first["premium"] == "540.00" and first["accept_path"] == f"/c/renewal/{first['renewal_id']}"
    async with use_tenant(tid):
        safe = wrap(mongo_db)
        types = {e["type"] async for e in safe.events.find({"payload.renewal_id": first["renewal_id"]})}
    assert {"RenewalQuoteRecorded", "TmdFitChecked", "GeneralAdviceWarningGiven", "RenewalClientDecision",
            "payment.advised"} <= types
    assert "ClientAcceptanceRecorded" not in types and "RenewalSigned" not in types
    second = await demo_renewal.prepare(app, mongo_db, tid, "demo-operator")
    assert second["reset"] == [first["renewal_id"]] and second["renewal_id"] != first["renewal_id"]
    async with use_tenant(tid):
        assert await safe.events.count_documents({"type": "RenewalDemoReset", "payload.renewal_id": first["renewal_id"]}) == 1
        old = await safe.payables.find_one({"envelope_id": {"$exists": True}, "state": "void"})
    assert old is not None
