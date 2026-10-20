"""R3.2 desktop Activity: payments panel (always dues, filter-independent), demo payment-due seed idempotent, layout DOM/CSS."""
from __future__ import annotations

import pathlib
import re

from app.scripts import r32_demo_payment_due as seed32
from tests.test_r12_auth import run

FE = pathlib.Path(__file__).resolve().parents[2] / "frontend/src"


def test_demo_payment_due_seed_is_idempotent():
    async def go(db, c, app):
        a = await seed32.seed(db)
        b = await seed32.seed(db)
        assert a["payable_id"] == b["payable_id"] and a["crn"] == b["crn"]
        rows = [p async for p in db.payables.find({"envelope_id": seed32.ENVELOPE})]
        assert len(rows) == 1 and rows[0]["state"] == "owed" and rows[0]["kind"] == "invoice"
        assert await db.events.count_documents({"type": "DemoPaymentDueSeeded"}) == 1  # same-day re-run is one ledger row
    run(go, {})


def test_activity_layout_has_sticky_payments_panel_on_desktop():
    tsx = (FE / "pages/client/Activity.tsx").read_text()
    for tid in ("mer-payments-panel", "mer-activity-feed-col", "mer-total-due", "mer-payments-empty", "mer-last-received",
                "mer-coming-up", "mer-coming-renewal", "mer-coming-claims", "mer-pay-${d.payable_id}", "mer-copy-crn-${d.payable_id}"):
        assert f'data-testid="{tid}"' in tsx or f"testid={{`{tid}`}}" in tsx, tid
    assert "I've paid this" in tsx and "Nothing to pay right now" in tsx
    css = (FE / "styles/meridian.css").read_text()
    desk = css[css.index("R3.2"):]
    assert re.search(r"@media \(min-width: 1024px\)[^}]*62fr[^}]*38fr", desk)
    assert "position: sticky" in desk and "@media (min-width: 768px)" in desk
    assert ".mer-act { display: flex; flex-direction: column; }" in desk  # <768px stays single column
