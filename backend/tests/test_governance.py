"""R0.5 — kill-switch matching + spend estimate (pure)."""
from app.kernel import governance, spend

T1, T2 = "tenant-1", "tenant-2"


def _row(kind, ref=None, tenant=None):
    return {"switch_id": f"{kind}-{ref}", "scope_kind": kind, "scope_ref": ref, "tenant_id": tenant, "enabled": True}


def test_agent_switch_matches_only_that_agent():
    rows = [_row("agent", "intake")]
    assert governance.match(rows, "intake", T1)
    assert governance.match(rows, "placement", T1) is None


def test_package_switch_matches_every_agent_in_package():
    rows = [_row("package", "spine")]
    assert governance.match(rows, "intake", T1) and governance.match(rows, "placement", T1)
    assert governance.match(rows, "liaison", T1) is None


def test_global_switch_matches_everything():
    assert governance.match([_row("global")], "liaison", T2)


def test_tenant_filter():
    rows = [_row("agent", "intake", T1)]
    assert governance.match(rows, "intake", T1)
    assert governance.match(rows, "intake", T2) is None


def test_no_rows_blocks_nothing():
    assert governance.match([], "intake", T1) is None


def test_cost_estimate_sonnet_rates():
    # 1M in × $3 + 1M out × $15
    assert spend.cost_usd("intake", 1_000_000, 1_000_000) == 18.0
    assert spend.cost_usd("docs", 1_000_000, 1_000_000) == 0.0


def test_thresholds_default_to_cap_pct(monkeypatch):
    monkeypatch.setenv("SPEND_CAP_USD", "10")
    monkeypatch.delenv("SPEND_ALERT_USD_50", raising=False)
    monkeypatch.setenv("SPEND_ALERT_USD_80", "7")
    assert dict(spend.thresholds()) == {50: 5.0, 80: 7.0, 100: 10.0}
