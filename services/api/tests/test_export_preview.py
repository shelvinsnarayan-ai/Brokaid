"""Phase 15 — export preview count matches the actual filter."""
from __future__ import annotations

import pytest


pytestmark = pytest.mark.asyncio


async def test_export_preview_count_matches(app_client, scene_seeded):
    _, client = app_client
    op = next(o for o in scene_seeded["operators"] if o["role"] == "principal")
    r = await client.post("/api/auth/dev/impersonate", json={"email": op["email"]})
    assert r.status_code == 200

    r_preview = await client.get("/api/trail/export/preview")
    assert r_preview.status_code == 200
    preview = r_preview.json()
    assert "event_count" in preview
    assert "event_types_summary" in preview

    r_bundle = await client.get("/api/trail/export/bundle")
    assert r_bundle.status_code == 200
    import io, json, zipfile
    z = zipfile.ZipFile(io.BytesIO(r_bundle.content))
    doc = json.loads(z.read("export.json"))
    assert preview["event_count"] == len(doc["events"]), (
        f"preview said {preview['event_count']}; bundle has {len(doc['events'])}"
    )
    # Types summary aligns.
    from collections import Counter
    bundle_types = Counter(e["type"] for e in doc["events"])
    preview_types = {t["type"]: t["count"] for t in preview["event_types_summary"]}
    for t, n in bundle_types.items():
        assert preview_types.get(t) == n
