"""Phase 15 — export preview:
  * unfiltered preview matches the bundle we would download.
  * date-range filters return 200 and narrow monotonically.
  * per-type breakdown of a windowed preview is a subset of the
    unfiltered breakdown.
  * regression: hitting the route with `from` and `to` never 500s.
"""
from __future__ import annotations

import io
import json
import zipfile
from collections import Counter
from datetime import timedelta, timezone

import pytest


pytestmark = pytest.mark.asyncio


async def _login_operator(client, scene_seeded):
    op = next(o for o in scene_seeded["operators"] if o["role"] == "principal")
    r = await client.post("/api/auth/dev/impersonate", json={"email": op["email"]})
    assert r.status_code == 200, r.text


async def test_export_preview_count_matches(app_client, scene_seeded):
    _, client = app_client
    await _login_operator(client, scene_seeded)

    r_preview = await client.get("/api/trail/export/preview")
    assert r_preview.status_code == 200
    preview = r_preview.json()
    assert "event_count" in preview
    assert "event_types_summary" in preview

    r_bundle = await client.get("/api/trail/export/bundle")
    assert r_bundle.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r_bundle.content))
    doc = json.loads(z.read("export.json"))
    assert preview["event_count"] == len(doc["events"]), (
        f"preview said {preview['event_count']}; bundle has {len(doc['events'])}"
    )
    bundle_types = Counter(e["type"] for e in doc["events"])
    preview_types = {t["type"]: t["count"] for t in preview["event_types_summary"]}
    for t, n in bundle_types.items():
        assert preview_types.get(t) == n


async def _all_events(client):
    """Load the full unfiltered bundle and return its events."""
    r_bundle = await client.get("/api/trail/export/bundle")
    assert r_bundle.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r_bundle.content))
    return json.loads(z.read("export.json"))["events"]


async def test_preview_regression_from_and_to_do_not_500(
    app_client, scene_seeded,
):
    """Bug reported by phase-15 verification: /api/trail/export/preview
    returned 500 whenever `from` or `to` were provided.
    """
    _, client = app_client
    await _login_operator(client, scene_seeded)
    r = await client.get(
        "/api/trail/export/preview?from=2024-01-01T00:00:00Z"
        "&to=2024-01-02T00:00:00Z"
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Empty window in the past → zero events, empty breakdown.
    assert body["event_count"] == 0
    assert body["event_types_summary"] == []


@pytest.mark.parametrize(
    "shape", ["no_filter", "from_only", "to_only", "from_and_to"],
)
async def test_preview_narrows_monotonically(
    app_client, scene_seeded, shape,
):
    """Total count monotonically decreases as the window narrows."""
    _, client = app_client
    await _login_operator(client, scene_seeded)

    events = await _all_events(client)
    if not events:
        pytest.skip("scene seed has no events")

    tss = sorted(e["ts"] for e in events)
    # Parse the earliest and latest ts, then narrow around the median.
    from datetime import datetime
    to_dt = datetime.fromisoformat(tss[-1].replace("Z", "+00:00"))
    from_dt = datetime.fromisoformat(tss[0].replace("Z", "+00:00"))
    median_dt = from_dt + (to_dt - from_dt) / 2

    def _z(dt):
        # Force UTC and use 'Z' suffix so the query string survives
        # URL-encoding (a bare '+' would decode to a space).
        return dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat() + "Z"

    baseline_count = len(events)
    qs = ""
    if shape == "from_only":
        qs = f"?from={_z(median_dt)}"
    elif shape == "to_only":
        qs = f"?to={_z(median_dt)}"
    elif shape == "from_and_to":
        # Narrow window: median ± 1 minute.
        lo = _z(median_dt - timedelta(minutes=1))
        hi = _z(median_dt + timedelta(minutes=1))
        qs = f"?from={lo}&to={hi}"

    r = await client.get(f"/api/trail/export/preview{qs}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["event_count"] <= baseline_count, (
        f"shape={shape}: windowed count {body['event_count']} "
        f"exceeds baseline {baseline_count}"
    )


async def test_preview_windowed_types_subset_of_unfiltered(
    app_client, scene_seeded,
):
    _, client = app_client
    await _login_operator(client, scene_seeded)

    events = await _all_events(client)
    if not events:
        pytest.skip("scene seed has no events")

    unfiltered_types = {e["type"] for e in events}

    # Narrow around the first event's ts.
    from datetime import datetime
    first = datetime.fromisoformat(events[0]["ts"].replace("Z", "+00:00"))
    lo = (first - timedelta(minutes=1)).astimezone(timezone.utc).replace(
        tzinfo=None
    ).isoformat() + "Z"
    hi = (first + timedelta(minutes=1)).astimezone(timezone.utc).replace(
        tzinfo=None
    ).isoformat() + "Z"

    r = await client.get(f"/api/trail/export/preview?from={lo}&to={hi}")
    assert r.status_code == 200, r.text
    windowed_types = {t["type"] for t in r.json()["event_types_summary"]}
    assert windowed_types <= unfiltered_types, (
        f"windowed breakdown introduced unseen type(s): "
        f"{windowed_types - unfiltered_types}"
    )
