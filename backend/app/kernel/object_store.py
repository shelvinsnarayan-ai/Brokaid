"""Emergent Object Storage (integration playbook). No delete, no public URLs: files are served by our own routes."""
from __future__ import annotations

import asyncio
import os

import requests

APP_NAME = "brokaid"
_key: str | None = None


def _url() -> str:
    base = (os.environ.get("INTEGRATION_PROXY_URL") or "").strip() or "https://integrations.emergentagent.com"
    return base.rstrip("/") + "/objstore/api/v1/storage"


def init_storage(force: bool = False) -> str:
    global _key
    if _key and not force:
        return _key
    resp = requests.post(f"{_url()}/init", json={"emergent_key": os.environ["EMERGENT_LLM_KEY"]}, timeout=30)
    resp.raise_for_status()
    _key = resp.json()["storage_key"]
    return _key


def _call(method: str, path: str, **kw) -> requests.Response:
    for attempt in (0, 1):
        resp = requests.request(method, f"{_url()}/objects/{path}",
                                headers={"X-Storage-Key": init_storage(force=attempt == 1), **kw.pop("headers", {})},
                                timeout=120, **kw)
        if resp.status_code != 404 or attempt or method != "PUT":
            break
    resp.raise_for_status()
    return resp


def _local(path: str):
    """Test seam: tests/conftest.py points this at a temp dir so pytest never calls the real store."""
    from pathlib import Path
    root = os.environ.get("OBJECT_STORE_LOCAL_DIR")
    return Path(root, path) if root else None


async def put_object(path: str, data: bytes, content_type: str) -> dict:
    if (p := _local(path)) is not None:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return {"path": path, "size": len(data)}
    return (await asyncio.to_thread(_call, "PUT", path, data=data, headers={"Content-Type": content_type})).json()


async def get_object(path: str) -> tuple[bytes, str]:
    if (p := _local(path)) is not None:
        return p.read_bytes(), "application/octet-stream"
    resp = await asyncio.to_thread(_call, "GET", path)
    return resp.content, resp.headers.get("Content-Type", "application/octet-stream")
