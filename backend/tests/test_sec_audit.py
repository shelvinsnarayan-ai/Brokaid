"""Security audit fixes: SEC-001 dev-mode fail-closed, SEC-002 CSV injection, CORS, NoSQL operators, XLSX limits."""
from __future__ import annotations

import csv
import io
import re
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.auth.dispatch import create_session_scoped
from app.auth.sessions import COOKIE_NAME
from app.db.tenant_safe import wrap
from app.kernel import devmode
from app.kernel import iadviser as ia
from app.kernel.csvsafe import safe_csv_cell
from app.kernel.tenant_ctx import use_tenant
from app.middleware.security import has_operator_key

APP = Path(__file__).resolve().parent.parent / "app"


# ---- SEC-001a: fail closed ------------------------------------------------
@pytest.mark.parametrize("env,expect", [(None, "production"), ("", "production"), ("staging", "production"),
                                        ("PROD", "production"), ("production", "production"), ("preview", "preview"),
                                        ("development", "development"), ("test", "test")])
def test_unknown_environment_is_production(monkeypatch, env, expect):
    if env is None:
        monkeypatch.delenv("ENVIRONMENT", raising=False)
    else:
        monkeypatch.setenv("ENVIRONMENT", env)
    assert devmode.environment() == expect


def test_dev_mode_needs_explicit_env_and_flag(monkeypatch):
    from app.settings import _assert_safe_to_start
    monkeypatch.setenv("AUTH_DEV_MODE", "true")
    monkeypatch.setenv("APP_BASE_URL", "https://x.preview.emergentagent.com")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    assert not devmode.dev_mode()
    _assert_safe_to_start()  # R3.10: never raises; logs CRITICAL and runs as production
    assert any("AUTH_DEV_MODE=true is ignored" in w for w in devmode.config_warnings())
    monkeypatch.setenv("ENVIRONMENT", "preview")
    assert devmode.dev_mode()
    monkeypatch.setenv("AUTH_DEV_MODE", "false")
    assert not devmode.dev_mode()


# ---- SEC-001b: dev shortcuts only for demo tenants -------------------------
def test_demo_tenant_rule():
    assert devmode.is_demo_row({"slug": "harbourline"}) and devmode.is_demo_row({"slug": "ci-alpha"})
    assert devmode.is_demo_row({"slug": "import-test", "test": True}) and devmode.is_demo_row({"slug": "x", "test": True})
    for t in ({"slug": "burmac"}, {"slug": "burmac", "test": True}, {"slug": "platform", "kind": "platform"},
              {"slug": "newco", "kind": "brokerage", "test": True}, {"slug": "anything"}, None):
        assert not devmode.is_demo_row(t), t


async def _tenant(db, slug, **extra):
    tid = str(uuid.uuid4())
    await db.tenants.insert_one({"tenant_id": tid, "slug": slug, "display_name": slug, "genesis_hash": "0" * 64,  # tenant-safe: system-only
                                 "created_at": None, **extra})
    return tid


async def _operator(db, tid, role="operator"):
    uid, email = str(uuid.uuid4()), f"op-{uuid.uuid4().hex[:6]}@sec.example"
    async with use_tenant(tid):
        await wrap(db).users.insert_one({"user_id": uid, "email": email, "kind": "operator", "display_name": "Op"})
        await wrap(db).user_brokerage_roles.insert_one({"user_id": uid, "tenant_id": tid, "brokerage_id": tid, "role": role,
                                                        "active": True})
    return email


@pytest.mark.asyncio
async def test_impersonation_refused_for_real_tenants_and_platform_admin(app_client, mongo_db, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("AUTH_DEV_MODE", "true")
    _, http = app_client
    demo = await _operator(mongo_db, await _tenant(mongo_db, f"ci-sec-{uuid.uuid4().hex[:4]}"))
    assert (await http.post("/api/auth/dev/impersonate", json={"email": demo})).status_code == 200
    for t in ({"slug": f"real-{uuid.uuid4().hex[:4]}", "kind": "brokerage"}, {"slug": f"plain-{uuid.uuid4().hex[:4]}"}):
        email = await _operator(mongo_db, await _tenant(mongo_db, **t))
        assert (await http.post("/api/auth/dev/impersonate", json={"email": email})).status_code == 404, t
    pa = await _operator(mongo_db, await _tenant(mongo_db, f"ci-pa-{uuid.uuid4().hex[:4]}"), role="platform_admin")
    assert (await http.post("/api/auth/dev/impersonate", json={"email": pa})).status_code == 404


@pytest.mark.asyncio
async def test_require_dev_tenant(mongo_db, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("AUTH_DEV_MODE", "true")
    demo = await _tenant(mongo_db, f"ci-d-{uuid.uuid4().hex[:4]}")
    await devmode.require_dev_tenant(mongo_db, demo, "operator")
    for tid, role in ((demo, "platform_admin"), (await _tenant(mongo_db, "burmac-copy", kind="brokerage"), "operator"),
                      (await _tenant(mongo_db, f"p-{uuid.uuid4().hex[:4]}", kind="platform"), "operator"), (None, "operator")):
        with pytest.raises(HTTPException) as e:
            await devmode.require_dev_tenant(mongo_db, tid, role)
        assert e.value.status_code == 404
    monkeypatch.setenv("AUTH_DEV_MODE", "false")
    with pytest.raises(HTTPException):
        await devmode.require_dev_tenant(mongo_db, demo, "operator")


# ---- SEC-001c: boot guard + import guard ------------------------------------
@pytest.mark.asyncio
async def test_boot_guard_refuses_dev_mode_beside_real_data(mongo_db, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("AUTH_DEV_MODE", "true")
    monkeypatch.setenv("APP_BASE_URL", "https://x.preview.emergentagent.com")
    monkeypatch.setattr(devmode, "_forced_off", None)
    await devmode.boot_guard(mongo_db)  # only demo/test tenants so far
    assert devmode.dev_mode()
    live = await _tenant(mongo_db, f"live-{uuid.uuid4().hex[:4]}", kind="brokerage", status="live")
    await devmode.boot_guard(mongo_db)  # R3.10: no crash-loop — dev mode is switched off instead
    assert not devmode.dev_mode() and "real tenant data" in devmode._forced_off
    await mongo_db.tenants.update_one({"tenant_id": live}, {"$set": {"status": "setup"}})  # tenant-safe: system-only
    await devmode.boot_guard(mongo_db)
    assert devmode.dev_mode()
    await mongo_db.import_runs.insert_one({"tenant_id": live, "run_id": "r"})  # tenant-safe: system-only
    await devmode.boot_guard(mongo_db)
    assert not devmode.dev_mode() and "imported data" in devmode._forced_off
    await mongo_db.import_runs.delete_many({"tenant_id": live})  # tenant-safe: system-only
    await mongo_db.tenants.delete_one({"tenant_id": live})  # tenant-safe: system-only
    monkeypatch.setenv("AUTH_DEV_MODE", "false")
    await devmode.boot_guard(mongo_db)  # dev mode off → production data is allowed


@pytest.mark.asyncio
async def test_import_into_real_tenant_only_in_production(mongo_db, monkeypatch):
    real = await _tenant(mongo_db, f"r-{uuid.uuid4().hex[:4]}", kind="brokerage")
    demo = await _tenant(mongo_db, f"ci-i-{uuid.uuid4().hex[:4]}")
    monkeypatch.setenv("ENVIRONMENT", "preview")
    await devmode.require_import_allowed(mongo_db, demo)
    with pytest.raises(HTTPException) as e:
        await devmode.require_import_allowed(mongo_db, real)
    assert e.value.status_code == 403
    monkeypatch.setenv("ENVIRONMENT", "production")
    await devmode.require_import_allowed(mongo_db, real)


# ---- SEC-001d: release check sees dev routes --------------------------------
def test_dev_routes_absent_in_production_app(monkeypatch):
    from app.kernel.release_check import exposed_dev_routes
    from app.main import create_app
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("AUTH_DEV_MODE", "true")
    dev = exposed_dev_routes(create_app(seed_dev_users=False))
    assert "/api/auth/dev/impersonate" in dev and any(p.startswith("/api/auth/operator") for p in dev)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_DEV_MODE", "false")
    monkeypatch.setenv("CORS_ORIGINS", "https://app.brokaid.example")
    assert exposed_dev_routes(create_app(seed_dev_users=False)) == []
    monkeypatch.delenv("ENVIRONMENT")  # unset = production: still nothing mounted
    monkeypatch.setenv("AUTH_DEV_MODE", "false")
    assert exposed_dev_routes(create_app(seed_dev_users=False)) == []
    # R3.10: dev env + flag but a public APP_BASE_URL → nothing mounted
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("AUTH_DEV_MODE", "true")
    monkeypatch.setenv("APP_BASE_URL", "https://www.brokaid.com")
    assert exposed_dev_routes(create_app(seed_dev_users=False)) == []


# ---- CORS ------------------------------------------------------------------
def test_cors_never_wildcard_and_production_refuses_it(monkeypatch):
    from app.settings import _assert_safe_to_start, cors_origins
    monkeypatch.setenv("APP_BASE_URL", "https://app.brokaid.example")
    monkeypatch.setenv("CORS_ORIGINS", "*,https://admin.brokaid.example")
    assert cors_origins() == ["https://admin.brokaid.example", "https://app.brokaid.example"]
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_DEV_MODE", "false")
    _assert_safe_to_start()  # R3.10: wildcard ignored with a warning, never a crash
    assert any("CORS_ORIGINS='*' is ignored" in w for w in devmode.config_warnings())
    monkeypatch.setenv("EXTRA_ALLOWED_ORIGINS", "https://ops.brokaid.example")
    from app.middleware.security import allowed_origins
    assert {"https://app.brokaid.example", "https://admin.brokaid.example", "https://ops.brokaid.example"} <= set(allowed_origins())


# ---- SEC-002: CSV formula injection ------------------------------------------
def test_safe_csv_cell():
    for bad in ("=1+1", "+cmd", "-2+3", "@SUM(A1)", "\t=x", "\r=x"):
        assert safe_csv_cell(bad).startswith("'"), bad
    assert safe_csv_cell("=HYPER\x00LINK") == "'=HYPERLINK"
    assert safe_csv_cell("a\x07b\nc") == "ab\nc"
    assert safe_csv_cell("Kai Hilux") == "Kai Hilux" and safe_csv_cell(-5) == -5 and safe_csv_cell(None) is None


def test_every_csv_writer_is_safe():
    """Sweep: no raw csv.writer/DictWriter in app code; every CSV-producing module uses safe_writer."""
    allowed = {"kernel/csvsafe.py", "scripts/iadviser_synth.py"}  # synth builds import *input* fixtures, not exports
    for p in APP.rglob("*.py"):
        rel = str(p.relative_to(APP))
        src = p.read_text()
        if rel in allowed:
            continue
        assert not re.search(r"csv\.(writer|DictWriter)\(", src), rel
        if "text/csv" in src and re.search(r"media_type=\"text/csv\"", src):
            assert "safe_writer(" in src, rel


def test_csv_routes_are_known():
    from app.kernel.routes_util import all_routes
    from app.main import create_app
    paths = {r.path for r in all_routes(create_app(seed_dev_users=False)) if r.path.endswith(".csv") or "shadow-mode/export" in r.path}
    assert paths == {"/api/o/import/runs/{run_id}/errors.csv", "/api/o/bank/reconciliation/export.csv",
                     "/api/o/compliance/complaints-register.csv", "/api/o/shadow-mode/export", "/api/o/renewal-review.csv"}, paths


@pytest.mark.asyncio
async def test_complaints_register_csv_neutralises_formulas(app_client, mongo_db):
    from tests.test_r17_compliance import _setup
    _, http = app_client
    tid, _, ids, ck = await _setup(mongo_db)
    async with use_tenant(tid):
        await wrap(mongo_db).clients.update_one({"client_id": "c1"}, {"$set": {"display_name": '=HYPERLINK("http://evil","x")'}})
    assert (await http.post("/api/c/complaints", headers=ck["owner"], json={"category": "service", "summary": "s"})).status_code == 200
    s = await create_session_scoped(mongo_db, tid, user_id=ids["principal"], brokerage_id="b", active_role="principal",
                                    mfa_verified=True, ttl=timedelta(minutes=10))
    r = await http.get("/api/o/compliance/complaints-register.csv", headers={"Cookie": f"{COOKIE_NAME}={s.session_id}"})
    assert r.status_code == 200, r.text
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[1][1].startswith("'=HYPERLINK")
    assert not any(c.startswith(("=", "+", "@")) for row in rows for c in row)


# ---- NoSQL operator injection -------------------------------------------------
def test_operator_key_detector():
    assert has_operator_key({"email": {"$ne": None}}) and has_operator_key([{"a": [{"$where": "1"}]}])
    assert not has_operator_key({"note": "costs $5", "mapping": {"premium": "$ Premium"}})


@pytest.mark.asyncio
async def test_operator_injection_rejected_centrally(app_client):
    _, http = app_client
    r = await http.post("/api/auth/login", json={"email": {"$ne": None}, "password": {"$gt": ""}})
    assert r.status_code == 400 and r.json()["detail"] == "invalid_field_name"
    r = await http.post("/api/auth/login", json={"email": "nobody@sec.example", "password": "costs $5"})
    assert r.status_code != 400


# ---- XLSX limits ---------------------------------------------------------------
def _xlsx(sheets=1, rows=3) -> bytes:
    import openpyxl
    wb = openpyxl.Workbook()
    for i in range(sheets):
        ws = wb.active if i == 0 else wb.create_sheet(f"S{i}")
        ws.append(["Client name", "ABN"])
        for r in range(rows):
            ws.append([f"C{r}", "51824753556"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_xlsx_limits(monkeypatch):
    assert ia.parse(_xlsx(), "x.xlsx")[0]["rows"]
    monkeypatch.setattr(ia, "MAX_ROWS", 5)
    with pytest.raises(ia.FileTooLarge, match="rows"):
        ia.parse(_xlsx(rows=10), "x.xlsx")
    with pytest.raises(ia.FileTooLarge, match="rows"):
        ia.parse(b"a,b\n" + b"1,2\n" * 10, "x.csv")
    monkeypatch.setattr(ia, "MAX_SHEETS", 2)
    with pytest.raises(ia.FileTooLarge, match="sheets"):
        ia.parse(_xlsx(sheets=3), "x.xlsx")
    monkeypatch.setattr(ia, "MAX_XLSX_UNCOMPRESSED", 1000)
    with pytest.raises(ia.FileTooLarge, match="expands"):
        ia.parse(_xlsx(), "x.xlsx")


# ---- logs carry no tokens or PII ---------------------------------------------------
def test_access_log_route_never_carries_tokens_or_emails():
    import logging
    import app.main  # noqa: F401 — disables uvicorn's raw-path access log
    from app.kernel.ops import _route
    req = type("R", (), {"scope": {}, "url": type("U", (), {"path": "/api/auth/invite/0lBCxta2Z-UuUSnGzJR_e-ePMFH/kai@fictionalclient.example"})()})()
    out = _route(req)
    assert "0lBCxta2Z" not in out and "@" not in out
    assert logging.getLogger("uvicorn.access").disabled


# ---- P3: $-keys in query params and form/multipart fields ------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["/api/c/things?$where=1", "/api/c/things?email[$ne]=x", '/api/c/things?filter={"$ne":null}'])
async def test_operator_in_query_rejected(app_client, url):
    _, http = app_client
    r = await http.get(url)
    assert r.status_code == 400 and r.json()["detail"] == "invalid_field_name"


@pytest.mark.asyncio
async def test_operator_in_form_and_multipart_rejected(app_client):
    _, http = app_client
    for kw in ({"data": {"$ne": "x"}}, {"data": {"email[$gt]": ""}}, {"data": {"meta": '{"a": {"$ne": 1}}'}},
               {"data": {"$where": "1"}, "files": {"file": ("a.csv", b"a,b\n1,2\n", "text/csv")}},
               {"data": {"meta": '[{"$in": []}]'}, "files": {"file": ("a.csv", b"a\n", "text/csv")}}):
        r = await http.post("/api/o/import/files", **kw)
        assert r.status_code == 400 and r.json()["detail"] == "invalid_field_name", kw


@pytest.mark.asyncio
async def test_ordinary_dollars_still_pass(app_client):
    _, http = app_client
    assert (await http.get("/api/c/things?note=costs+$5")).status_code != 400
    r = await http.post("/api/o/import/files", data={"note": "costs $5"}, files={"file": ("a.csv", b"Premium $\n5\n", "text/csv")})
    assert r.status_code != 400  # 401: unauthenticated, but not rejected as an operator


def test_cors_lists_are_explicit():
    from app.main import CORS_HEADERS, CORS_METHODS, create_app
    assert "*" not in CORS_METHODS and "*" not in CORS_HEADERS
    assert set(CORS_METHODS) == {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
    app = create_app(seed_dev_users=False)
    cors = next(m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware")
    assert cors.kwargs["allow_methods"] == CORS_METHODS and cors.kwargs["allow_headers"] == CORS_HEADERS


@pytest.mark.asyncio
async def test_cors_preflight_refuses_unknown_header(app_client, monkeypatch):
    _, http = app_client
    from app.middleware.security import allowed_origins
    origin = sorted(allowed_origins())[0]
    ok = await http.options("/api/health", headers={"Origin": origin, "Access-Control-Request-Method": "POST",
                                                    "Access-Control-Request-Headers": "content-type"})
    assert ok.status_code == 200
    bad = await http.options("/api/health", headers={"Origin": origin, "Access-Control-Request-Method": "POST",
                                                     "Access-Control-Request-Headers": "x-evil"})
    assert bad.status_code == 400


def test_release_check_guards_use_one_prefix_list():
    src = (APP / "kernel" / "release_check.py").read_text()
    assert src.count('"/api/auth/dev"') == 1 and "/api/_admin" in src  # only DEV_ROUTE_PREFIXES names them
