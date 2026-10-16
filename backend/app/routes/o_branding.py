"""R0.15 — brokerage email branding editor (caller's own tenant only) + public logo route for emails."""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from pydantic import BaseModel, Field

from app.auth.sessions import Session
from app.db.tenant_safe import wrap
from app.kernel import branding, email_channel, notify, object_store
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch
from app.middleware.session import require_operator_session

router = APIRouter(prefix="/api")
_LOGO_CACHE: dict[str, tuple[bytes, str]] = {}


def viewer(session: Session = Depends(require_operator_session)) -> Session:
    if session.active_role not in ("operator", "principal"):
        raise HTTPException(status_code=403, detail="branding_view_forbidden")
    return session


def editor(session: Session = Depends(require_operator_session)) -> Session:
    if session.active_role != "operator":
        raise HTTPException(status_code=403, detail="operator_role_required")
    return session


class BrandingBody(BaseModel):
    sender_name: str = ""
    reply_to: str | None = None
    brand_colour: str = ""
    logo_url: str | None = None
    allowlist: list[str] = Field(default_factory=list)


class DraftBody(BaseModel):
    draft: BrandingBody | None = None


def _db(request: Request):
    return request.app.state.mongo_db


async def _tenant(request: Request, session: Session) -> dict:
    # tenant-safe: system-only — the caller's own tenant record, by session tenant_id.
    return await _db(request).tenants.find_one({"tenant_id": session.tenant_id}) or {}


def _stored(t: dict) -> dict:
    s = email_channel.resolve_settings(t)
    return {k: s[k] for k in branding.FIELDS}


async def _logo_asset(request: Request, session: Session, url: str) -> dict | None:
    asset_id = url.rstrip("/").rsplit("/", 1)[-1].split(".", 1)[0]
    async with use_tenant(session.tenant_id):
        a = await wrap(_db(request)).brand_assets.find_one({"asset_id": asset_id, "is_deleted": False})
    return a if a and a["url"] == url else None


async def _clean(request: Request, session: Session, body: BrandingBody) -> tuple[dict, dict | None]:
    asset = await _logo_asset(request, session, body.logo_url) if body.logo_url else None
    try:
        clean = branding.validate(body.model_dump(), lambda _u: asset is not None)
    except branding.BrandingInvalid as e:
        raise HTTPException(status_code=422, detail={"code": "invalid_branding", "errors": e.errors}) from e
    return clean, asset


def _view(t: dict, session: Session) -> dict:
    return {"tenant_name": t.get("display_name"), "settings": _stored(t), "can_edit": session.active_role == "operator",
            "email_enabled": email_channel.email_enabled(), "min_contrast": branding.MIN_CONTRAST,
            "logo_max_bytes": branding.MAX_LOGO_BYTES, "logo_max_width": branding.MAX_LOGO_WIDTH}


@router.get("/o/tenant/branding")
async def get_branding(request: Request, session: Session = Depends(viewer)) -> dict:
    return _view(await _tenant(request, session), session)


@router.put("/o/tenant/branding")
async def put_branding(body: BrandingBody, request: Request, session: Session = Depends(editor)) -> dict:
    t = await _tenant(request, session)
    clean, asset = await _clean(request, session, body)
    payload = branding.audit_payload(_stored(t), clean, asset["sha256"] if asset else None)
    if payload["fields"]:
        # tenant-safe: system-only — writes the caller's own tenant record only.
        await _db(request).tenants.update_one({"tenant_id": session.tenant_id}, {"$set": {"email_settings": clean}})
        async with use_tenant(session.tenant_id):
            await append_via_dispatch(_db(request), session.tenant_id, "BrandingSettingsChanged",
                                      actor={"kind": "human", "id": session.user_id, "name": session.active_role},
                                      payload=payload, idempotency_key=f"branding:{uuid.uuid4()}")
    return {**_view(await _tenant(request, session), session), "changed": payload["fields"]}


@router.post("/o/tenant/branding/logo")
async def upload_logo(request: Request, file: UploadFile = File(...), session: Session = Depends(editor)) -> dict:
    data = await file.read(branding.MAX_LOGO_BYTES + 1)
    try:
        out, ctype, ext, w, h = branding.process_logo(data)
    except branding.LogoInvalid as e:
        raise HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message}) from e
    asset_id = uuid.uuid4().hex
    res = await object_store.put_object(f"{object_store.APP_NAME}/brand/{session.tenant_id}/{asset_id}.{ext}", out, ctype)
    url = f"{os.environ['APP_BASE_URL'].rstrip('/')}/api/public/brand/{asset_id}.{ext}"
    sha = hashlib.sha256(out).hexdigest()
    async with use_tenant(session.tenant_id):
        await wrap(_db(request)).brand_assets.insert_one({
            "asset_id": asset_id, "storage_path": res["path"], "content_type": ctype, "url": url, "sha256": sha,
            "width": w, "height": h, "size": len(out), "original_filename": (file.filename or "")[:120],
            "created_by": session.user_id, "created_at": datetime.now(timezone.utc), "is_deleted": False})
    return {"logo_url": url, "width": w, "height": h, "size": len(out), "sha256": sha}


async def _settings_for(request: Request, session: Session, draft: BrandingBody | None) -> tuple[dict, str]:
    t = await _tenant(request, session)
    if draft is None:
        return email_channel.resolve_settings(t), "saved"
    clean, _ = await _clean(request, session, draft)
    return email_channel.resolve_settings({"display_name": t.get("display_name"), "email_settings": clean}), "draft"


@router.post("/o/tenant/branding/preview")
async def preview(body: DraftBody, request: Request, session: Session = Depends(viewer)) -> dict:
    s, mode = await _settings_for(request, session, body.draft)
    async with use_tenant(session.tenant_id):
        me = await wrap(_db(request)).users.find_one({"user_id": session.user_id}) or {}
    subject, html, _ = email_channel.render({"kind": "broker_review", "link": "/o/alerts"}, me, s)
    return {"mode": mode, "subject": subject, "html": html}


@router.post("/o/tenant/branding/test-email")
async def test_email(body: DraftBody, request: Request, session: Session = Depends(editor)) -> dict:
    s, mode = await _settings_for(request, session, body.draft)
    key = f"branding-test:{uuid.uuid4()}"
    store = _db(request)
    await notify.deliver(store, session.tenant_id, session.user_id, kind="broker_review", title="Branding test",
                         many="Branding test", body="A branding test from Agent operations.", link="/o/alerts",
                         dedupe_key=key, idem=key, test=True)
    async with use_tenant(session.tenant_id):
        n = await wrap(store).notifications.find_one({"idem_keys": key})
    await email_channel.run_once(store, datetime.now(timezone.utc) + email_channel.SETTLE + timedelta(seconds=1),
                                 ids=[n["notification_id"]], ignore_quiet=True, settings_override=s)
    async with use_tenant(session.tenant_id):
        n = await wrap(store).notifications.find_one({"idem_keys": key})
    return {"mode": mode, "email": n["channels_state"].get("email"), "message_id": n.get("email_message_id")}


@router.get("/public/brand/{name}")
async def public_logo(name: str, request: Request) -> Response:
    """Unauthenticated on purpose: mail apps fetch logos without cookies. Unguessable ids, logos only."""
    asset_id = name.partition(".")[0]
    if asset_id not in _LOGO_CACHE:
        # tenant-safe: system-only — public brand logo by unguessable asset id; no tenant data beyond the image.
        a = await _db(request).brand_assets.find_one({"asset_id": asset_id, "is_deleted": False})
        if not a or not a["url"].endswith(f"/{name}"):
            raise HTTPException(status_code=404, detail="not_found")
        data, _ct = await object_store.get_object(a["storage_path"])
        _LOGO_CACHE[asset_id] = (data, a["content_type"])
    data, ctype = _LOGO_CACHE[asset_id]
    return Response(content=data, media_type=ctype, headers={"Cache-Control": "public, max-age=86400, immutable"})
