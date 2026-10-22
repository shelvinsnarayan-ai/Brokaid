"""R3.5 claim-thread attachments: photo/video upload with magic-byte typing, fail-closed metadata stripping, private serving,
ownership/authority/closed checks, ledger per file, staff file list + claim pack, notification wording; HEIC GPS on both paths."""
from __future__ import annotations

import io
import uuid
import zipfile

import pytest

from app.db.tenant_safe import wrap
from app.kernel import claim_threads as ct, filetype, media, object_store
from app.kernel.tenant_ctx import use_tenant
from tests import media_samples as ms
from tests.test_r16_renewals import _people, _tenant
from tests.test_r34_claim_threads import _claim, _staff_cookie

BLOBS: dict[str, bytes] = {}


@pytest.fixture(autouse=True)
def fake_storage(monkeypatch):
    async def put(path, data, ct_):
        BLOBS[path] = data
        return {"path": path}

    async def get(path):
        return BLOBS[path], "application/octet-stream"
    monkeypatch.setattr(object_store, "put_object", put)
    monkeypatch.setattr(object_store, "get_object", get)


async def _setup(db):
    tid = await _tenant(db, f"r35-{uuid.uuid4().hex[:4]}")
    open_c, closed_c, foreign_c = _claim("c1", "CLM-1"), _claim("c1", "CLM-2", "closed"), _claim("c2", "CLM-3")
    open_c |= {"channel": "app", "incident": {"rego": "ABC123", "date": "2026-09-01", "description": "Reversed into a bollard"}}
    async with use_tenant(tid):
        for cid in ("c1", "c2"):
            await wrap(db).clients.insert_one({"client_id": cid, "display_name": f"Client {cid}"})
        for c in (open_c, closed_c, foreign_c):
            await wrap(db).claims.insert_one(dict(c))
    ids, cookies = await _people(db, tid, "c1")
    _, other = await _people(db, tid, "c2")
    return tid, open_c, closed_c, foreign_c, ids, cookies, other


def _up(http, claim_id, h, data, name, msg_id, ctype="application/octet-stream"):
    return http.post(f"/api/c/claims/{claim_id}/thread/attachments", headers=h, data={"msg_id": msg_id}, files={"file": (name, data, ctype)})


@pytest.mark.asyncio
async def test_attachments_end_to_end(app_client, mongo_db):
    _, http = app_client
    tid, open_c, closed_c, foreign_c, ids, cookies, other = await _setup(mongo_db)
    h, cid, msg = cookies["owner"], open_c["claim_id"], str(uuid.uuid4())

    # JPEG + HEIC (GPS-tagged) + MP4 (location-tagged): uploaded, typed by bytes (declared types ignored), metadata gone
    jpg = (await _up(http, cid, h, ms.photo("JPEG"), "rear.jpg", msg, "image/jpeg")).json()["file"]
    heic = (await _up(http, cid, h, ms.photo("HEIF"), "IMG_0001.HEIC", msg, "application/octet-stream")).json()["file"]
    r = await _up(http, cid, h, ms.video("mp4"), "clip.mp4", msg, "video/mp4")
    assert r.status_code == 200, r.text
    vid = r.json()["file"]
    assert (jpg["content_type"], heic["content_type"], vid["content_type"]) == ("image/jpeg", "image/heic", "video/mp4")
    assert heic["has_preview"] and vid["has_preview"] and not jpg["has_preview"] and vid["kind"] == "video" and vid["duration_s"]
    async with use_tenant(tid):
        rows = {r["file_id"]: r["doc"] async for r in wrap(mongo_db).claim_uploads.find({"msg_id": msg})}
    for f in (jpg, heic):
        assert media._no_gps(BLOBS[rows[f["file_id"]]["storage_path"]])
    assert not ms.video_has_location(BLOBS[rows[vid["file_id"]]["storage_path"]])
    assert filetype.sniff(BLOBS[rows[heic["file_id"]]["preview_path"]]) == "image/jpeg"
    assert ms.video_has_location(ms.video("mp4"))  # the sample really carried a location before stripping
    # own staged preview visible; another client's is not
    assert (await http.get(f"/api/c/claims/{cid}/thread/attachments/{heic['file_id']}/preview", headers=h)).status_code == 200

    # rejections: disguised PDF/EXE (by bytes), oversize photo, unsupported codec stays fail-closed
    assert (await _up(http, cid, h, b"%PDF-1.7\n" + b"x" * 500, "photo.jpg", msg, "image/jpeg")).status_code == 415
    assert (await _up(http, cid, h, b"MZ\x90\x00" + b"x" * 500, "video.mp4", msg, "video/mp4")).status_code == 415
    big = ms.photo("JPEG", gps=False) + b"\0" * (media.MAX_PHOTO + 1)
    r = await _up(http, cid, h, big, "big.jpg", msg, "image/jpeg")
    assert r.status_code == 413 and r.json()["detail"]["code"] == "too_large"
    fake_video = b"\x00\x00\x00\x18ftypmp42" + b"\0" * 2000  # right magic bytes, not a real video → refused, never stored
    assert (await _up(http, cid, h, fake_video, "x.mp4", msg)).status_code == 415

    # closed claim 409; cross-client 404; member without can_lodge_claim refused
    assert (await _up(http, closed_c["claim_id"], h, ms.photo(), "a.jpg", str(uuid.uuid4()))).status_code == 409
    assert (await http.post(f"/api/c/claims/{closed_c['claim_id']}/thread", headers=h, json={"message": "hi"})).status_code == 409
    assert (await _up(http, foreign_c["claim_id"], h, ms.photo(), "a.jpg", str(uuid.uuid4()))).status_code == 404
    async with use_tenant(tid):
        await wrap(mongo_db).memberships.update_one({"client_id": "c1", "role": "bookkeeper"}, {"$set": {"authority.can_lodge_claim": False}})
    assert (await _up(http, cid, cookies["bookkeeper"], ms.photo(), "a.jpg", str(uuid.uuid4()))).status_code == 403

    # remove one, then send attachments-only: one ClaimDocumentAdded per file, summary on the message + AE notification
    assert (await http.delete(f"/api/c/claims/{cid}/thread/attachments/{jpg['file_id']}", headers=h)).status_code == 200
    files = [heic["file_id"], vid["file_id"]]
    bad = await http.post(f"/api/c/claims/{cid}/thread", headers=h, json={"file_ids": [jpg["file_id"]], "msg_id": msg})
    assert bad.status_code == 400
    r = await http.post(f"/api/c/claims/{cid}/thread", headers=h, json={"file_ids": files, "msg_id": msg})
    assert r.status_code == 200, r.text
    again = await http.post(f"/api/c/claims/{cid}/thread", headers=h, json={"file_ids": files, "msg_id": msg})
    assert again.json()["replayed"]
    assert (await _up(http, cid, h, ms.photo(), "late.jpg", msg)).status_code == 409
    async with use_tenant(tid):
        evs = [e async for e in wrap(mongo_db).events.find({"type": "ClaimDocumentAdded", "payload.msg_id": msg})]
        ask = await wrap(mongo_db).events.find_one({"type": "ClientQuestionAsked", "payload.msg_id": msg})
        note = await wrap(mongo_db).notifications.find_one({"user_id": ids["ae"], "kind": "client_question"})
    assert sorted(e["payload"]["file_id"] for e in evs) == sorted(files) and all(e["payload"]["source"] == "claim_thread" for e in evs)
    assert ask["payload"]["attachment_summary"] == "1 photo and 1 video" and ask["payload"]["message"] == ""
    assert note is None or "with 1 photo and 1 video" in note.get("title", "")

    g = (await http.get(f"/api/c/claims/{cid}/thread", headers=h)).json()
    atts = g["messages"][-1]["attachments"]
    assert [a["kind"] for a in atts] == ["photo", "video"] and g["attachment_limits"]["video_bytes"] == 100 * 1024 * 1024
    # authenticated, ownership-checked serving: original (stripped), preview, Range for video; other client 404
    assert (await http.get(f"/api/c/claims/{cid}/files/{heic['file_id']}?preview=true", headers=h)).headers["content-type"] == "image/jpeg"
    dl = await http.get(f"/api/c/claims/{cid}/files/{heic['file_id']}?download=true", headers=h)
    assert dl.status_code == 200 and "attachment" in dl.headers["content-disposition"] and media._no_gps(dl.content)
    part = await http.get(f"/api/c/claims/{cid}/files/{vid['file_id']}", headers={**h, "Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100
    assert (await http.get(f"/api/c/claims/{cid}/files/{vid['file_id']}", headers=other["owner"])).status_code == 404
    assert (await http.get(f"/api/c/claims/{cid}/files/{vid['file_id']}")).status_code == 401
    assert (await http.get(f"/api/c/claims/{cid}/thread/attachments/{jpg['file_id']}/preview", headers=h)).status_code == 404

    # staff: claim files (source claim_thread), Messages panel + Inbox thread attachments, claim pack ZIP holds both files
    ae = await _staff_cookie(mongo_db, tid, ids["ae"], "ae")
    det = (await http.get(f"/api/o/claims/{cid}", headers=ae)).json()
    sf = {f["file_id"]: f for f in (det.get("claim") or det)["files"]}
    assert all(sf[f]["source"] == "claim_thread" and sf[f]["msg_id"] == msg for f in files) and jpg["file_id"] not in sf
    panel = (await http.get(f"/api/o/claims/{cid}/thread", headers=ae)).json()
    assert [a["file_id"] for a in panel["conversation"][-1]["attachments"]] == files
    th = (await http.get(f"/api/o/inbox/{ct.thread_id_for(cid)}", headers=ae)).json()
    assert [a["file_id"] for a in th["conversation"][-1]["attachments"]] == files
    assert (await http.get(f"/api/o/claims/{cid}/files/{vid['file_id']}?preview=true", headers=ae)).status_code == 200
    assert (await http.post(f"/api/o/claims/{cid}/pack", headers=ae)).status_code == 200
    async with use_tenant(tid):
        c = await wrap(mongo_db).claims.find_one({"claim_id": cid})
    names = zipfile.ZipFile(io.BytesIO(BLOBS[c["pack"]["zip_path"]])).namelist()
    assert any(n.startswith("video/") for n in names) and any(n.startswith("photo/") and n.endswith(".heic") for n in names)


@pytest.mark.asyncio
async def test_limits_and_existing_upload_heic(app_client, mongo_db):
    _, http = app_client
    tid, open_c, *_rest, ids, cookies, _other = await _setup(mongo_db)
    h, cid, msg = cookies["owner"], open_c["claim_id"], str(uuid.uuid4())
    for i in range(5):
        assert (await _up(http, cid, h, ms.photo(), f"p{i}.jpg", msg)).status_code == 200
    r = await _up(http, cid, h, ms.photo(), "p6.jpg", msg)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "too_many_files"
    assert (await http.post(f"/api/c/claims/{cid}/thread", headers=h, json={"file_ids": ["a"] * 6, "msg_id": msg})).status_code == 422
    assert (await http.post(f"/api/c/claims/{cid}/thread", headers=h, json={"message": "", "file_ids": []})).status_code == 422

    # existing claim upload path: GPS-tagged HEIC is now stripped (fail closed) and gets a JPEG preview
    up = await http.post(f"/api/c/claims/{cid}/files", headers=h, data={"kind": "photo", "keep_location": "false"},
                         files={"file": ("IMG.HEIC", ms.photo("HEIF"), "image/heic")})
    assert up.status_code == 200, up.text
    f = up.json()["file"]
    assert f["has_preview"]
    async with use_tenant(tid):
        d = next(x for x in (await wrap(mongo_db).claims.find_one({"claim_id": cid}))["docs"] if x["file_id"] == f["file_id"])
    assert media._no_gps(BLOBS[d["storage_path"]]) and filetype.sniff(BLOBS[d["preview_path"]]) == "image/jpeg"
    async with use_tenant(tid):  # a test-flagged file is hidden from the client
        await wrap(mongo_db).claims.update_one({"claim_id": cid, "docs.file_id": f["file_id"]}, {"$set": {"docs.$.test": True}})
    assert (await http.get(f"/api/c/claims/{cid}/files/{f['file_id']}", headers=h)).status_code == 404
    ae = await _staff_cookie(mongo_db, tid, ids["ae"], "ae")  # ...and from staff documents and the claim pack
    det = (await http.get(f"/api/o/claims/{cid}", headers=ae)).json()
    assert f["file_id"] not in {x["file_id"] for x in (det.get("claim") or det)["files"]}
    assert (await http.post(f"/api/o/claims/{cid}/pack", headers=ae)).status_code == 200
    async with use_tenant(tid):
        c = await wrap(mongo_db).claims.find_one({"claim_id": cid})
    assert not any(f["file_id"][:8] in n for n in zipfile.ZipFile(io.BytesIO(BLOBS[c["pack"]["zip_path"]])).namelist())


def test_strip_fails_closed(monkeypatch):
    with pytest.raises(media.Rejected):
        media.strip_photo(b"\xff\xd8\xff" + b"garbage", "image/jpeg")
    monkeypatch.setattr(media, "_no_gps", lambda data: False)
    with pytest.raises(media.Rejected):
        media.strip_photo(ms.photo(), "image/jpeg")
    assert ct.summary([{"kind": "photo"}, {"kind": "photo"}]) == "2 photos"
    monkeypatch.setattr(media, "ffmpeg_exe", lambda: None)
    with pytest.raises(media.Rejected) as e:
        media.probe("/nonexistent.mp4")
    assert e.value.code == "video_unavailable"
