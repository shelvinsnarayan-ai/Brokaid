"""R1.9 — monitoring: structured JSON access logs (no PII, no bodies), error capture, health/ready, Ops summary, owner alerts.

ops_requests / ops_errors / ops_alerts are platform-level system collections (no tenant data, tenant ids hashed).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

log = logging.getLogger("brokaid.access")
ALERT_THROTTLE = timedelta(hours=6)
ERROR_SPIKE = int(os.environ.get("OPS_ERROR_SPIKE_15M") or 10)
_SCRUB = [(re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<email>"), (re.compile(r"'[^']{0,200}'|\"[^\"]{0,200}\""), "<str>"),
          (re.compile(r"\b\d{4,}\b"), "<num>"), (re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"), "<uuid>")]


def now() -> datetime:
    return datetime.now(timezone.utc)


def thash(tid: str | None) -> str | None:
    return hashlib.sha256(tid.encode()).hexdigest()[:12] if tid else None


def scrub(text: str) -> str:
    for rx, rep in _SCRUB:
        text = rx.sub(rep, text)
    return text[:4000]


def _route(request) -> str:
    r = request.scope.get("route")
    return getattr(r, "path", None) or scrub(re.sub(r"[\w-]{16,}", "{id}", request.url.path))  # unmatched: no tokens/PII


def _store(request):
    return getattr(request.app.state, "mongo_db", None)


async def capture_error(store, exc: BaseException, route: str, request_id: str) -> dict:
    tb = traceback.extract_tb(exc.__traceback__)
    top = next((f for f in reversed(tb) if "/app/" in f.filename), tb[-1] if tb else None)
    fp = hashlib.sha256(f"{type(exc).__name__}:{top.filename if top else ''}:{top.lineno if top else ''}:{route}".encode()).hexdigest()[:16]
    t = now()
    # tenant-safe: system-only — platform error register (scrubbed, no tenant data)
    res = await store.ops_errors.find_one_and_update(
        {"fingerprint": fp},
        {"$inc": {"count": 1}, "$set": {"last_seen": t, "route": route, "type": type(exc).__name__, "last_request_id": request_id},
         "$setOnInsert": {"first_seen": t, "sample_stack": scrub("".join(traceback.format_exception(exc))),
                          "message": scrub(str(exc))[:300]}},
        upsert=True, return_document=True)
    if res.get("count") == 1:
        await owner_alert(store, f"error:new:{fp}", f"New server error: {type(exc).__name__} on {route}",
                          f"Fingerprint {fp}. First seen {t:%d/%m/%Y %H:%M} UTC. See Ops → Errors.")
    return res


class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        if not re.fullmatch(r"[\w-]{6,64}", rid):
            rid = uuid.uuid4().hex[:16]
        t0 = time.monotonic()
        status, store = 500, _store(request)
        try:
            resp = await call_next(request)
            status = resp.status_code
        except Exception as exc:  # noqa: BLE001 — capture, then a generic 500 with the request id
            log.error(json.dumps({"rid": rid, "error": type(exc).__name__, "stack": scrub("".join(traceback.format_exception(exc)))[-1500:]}))
            if store is not None:
                try:
                    await capture_error(store, exc, _route(request), rid)
                except Exception:  # noqa: BLE001
                    log.exception("error capture failed")
            resp = JSONResponse({"detail": "Something went wrong on our side.", "request_id": rid}, status_code=500)
        ms = round((time.monotonic() - t0) * 1000, 1)
        resp.headers["X-Request-ID"] = rid
        if request.url.path.startswith("/api/"):
            sess = getattr(request.state, "session", None)
            rec = {"at": now().isoformat(), "rid": rid, "method": request.method, "route": _route(request), "status": status, "ms": ms,
                   "tenant": thash(getattr(sess, "tenant_id", None)), "role": getattr(sess, "active_role", None)}
            log.info(json.dumps(rec))
            if store is not None and os.environ.get("OPS_REQUEST_LOG") != "0":
                asyncio.create_task(_record(store, rec))
        return resp


async def _record(store, rec):
    try:
        # tenant-safe: system-only — request timings (route template, no ids, no bodies)
        await store.ops_requests.insert_one({**rec, "at": now()})
    except Exception:  # noqa: BLE001
        pass


async def ensure_indexes(store) -> None:
    await store.ops_requests.create_index("at", expireAfterSeconds=3 * 86400, name="ops_requests_ttl")
    await store.ops_errors.create_index("fingerprint", unique=True, name="ops_errors_fp")


# ---- health / ready ----
async def ready(store) -> dict:
    checks = {}
    try:
        await store.command("ping")
        checks["mongo"] = {"ok": True}
    except Exception as e:  # noqa: BLE001
        checks["mongo"] = {"ok": False, "detail": type(e).__name__}
    try:
        from app.kernel import object_store
        if object_store._local("probe") is None:
            await asyncio.to_thread(object_store.init_storage)
        checks["object_storage"] = {"ok": True}
    except Exception as e:  # noqa: BLE001
        checks["object_storage"] = {"ok": False, "detail": type(e).__name__}
    lead = await store["_locks"].find_one({"_id": "scheduler"}) if store is not None else None  # tenant-safe: system-only
    exp = lead and lead.get("expires_at")
    alive = bool(exp and (exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)) > now())
    checks["job_leader"] = {"ok": alive, "owner": (lead or {}).get("owner")}
    checks["llm_key"] = {"ok": bool(os.environ.get("EMERGENT_LLM_KEY"))}
    from app.kernel import media, pwa
    ok = all(c["ok"] for c in checks.values())
    checks["media_ffmpeg"] = {"ok": bool(media.ffmpeg_exe()), "gates": "video uploads only"}  # missing → videos refused, app stays up
    sw = await pwa.cached()  # informational like ffmpeg: a broken SW stops web push, not the app
    checks["pwa_service_worker"] = {"ok": sw["ok"], "mode": sw.get("mode"), "gates": "web push only"}
    return {"ok": ok, "checks": checks}


# ---- Ops summary ----
async def summary(store) -> dict:
    t = now()
    since1, since24 = t - timedelta(hours=1), t - timedelta(hours=24)
    # tenant-safe: system-only — platform-level operational numbers
    req = store.ops_requests
    tot1, err1 = await req.count_documents({"at": {"$gte": since1}}), await req.count_documents({"at": {"$gte": since1}, "status": {"$gte": 500}})
    tot24, err24 = await req.count_documents({"at": {"$gte": since24}}), await req.count_documents({"at": {"$gte": since24}, "status": {"$gte": 500}})
    slow = [r async for r in req.aggregate([
        {"$match": {"at": {"$gte": since24}}},
        {"$group": {"_id": {"m": "$method", "r": "$route"}, "n": {"$sum": 1}, "ms": {"$push": "$ms"}}},
        {"$match": {"n": {"$gte": 3}}}])]
    for s in slow:
        ms = sorted(s["ms"])
        s["p95"] = ms[min(len(ms) - 1, int(len(ms) * 0.95))]
    slow = sorted(({"route": f"{s['_id']['m']} {s['_id']['r']}", "n": s["n"], "p95_ms": s["p95"]} for s in slow), key=lambda x: -x["p95_ms"])[:10]
    jobs = store.jobs
    depth = await jobs.count_documents({"status": "queued"})
    dead = await jobs.count_documents({"status": "dead"})
    dead_old = await jobs.count_documents({"status": "dead", "finished_at": {"$lte": t - timedelta(hours=1)}})
    async def rate(kind):
        done = await jobs.count_documents({"kind": kind, "finished_at": {"$gte": since24}, "status": {"$in": ["succeeded", "dead"]}})
        bad = await jobs.count_documents({"kind": kind, "finished_at": {"$gte": since24}, "status": "dead"})
        return {"done": done, "failed": bad, "rate": round(bad / done, 3) if done else 0}
    from app.kernel import spend
    cap = spend.cap_usd()
    usd = round(await spend_total(store), 2)
    rc = []
    async for tn in store.tenants.find({"kind": {"$ne": "platform"}}, {"tenant_id": 1, "slug": 1}):
        last = await store.release_check_runs.find_one({"tenant_id": tn["tenant_id"]}, {"_id": 0, "result": 1, "finished_at": 1, "started_at": 1},
                                                       sort=[("started_at", -1)])
        rc.append({"slug": tn["slug"], "result": (last or {}).get("result"), "at": _iso((last or {}).get("finished_at") or (last or {}).get("started_at"))})
    ev = await store.eval_runs.find_one({"partial": {"$ne": True}}, {"_id": 0, "summary": 1, "at": 1, "gate_pass": 1}, sort=[("at", -1)])
    errs = [{**{k: _iso(v) for k, v in e.items() if k not in ("_id", "sample_stack")}} async for e in
            store.ops_errors.find({}).sort("last_seen", -1).limit(20)]
    rd = await store.restore_drills.find_one({}, {"_id": 0}, sort=[("at", -1)])
    bk = await store.backups.find_one({}, {"_id": 0, "at": 1, "tenant_slug": 1}, sort=[("at", -1)])
    return {"at": t.isoformat(),
            "errors": {"rate_1h": round(err1 / tot1, 4) if tot1 else 0, "count_1h": err1, "requests_1h": tot1,
                       "rate_24h": round(err24 / tot24, 4) if tot24 else 0, "count_24h": err24, "requests_24h": tot24, "recent": errs},
            "slow_endpoints": slow, "jobs": {"queued": depth, "dead": dead, "dead_over_1h": dead_old},
            "email": await rate("email.send"), "push": await rate("push.send"),
            "spend": {"usd_month": usd, "cap_usd": cap, "pct": round(100 * usd / cap, 1) if cap else None},
            "release_checks": rc, "eval": ev and {**ev, "at": _iso(ev.get("at"))},
            "backups": {"last": bk and {**bk, "at": _iso(bk["at"])}, "last_drill": rd and {k: _iso(v) for k, v in rd.items()}},
            "ready": await ready(store)}


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


# ---- owner alerts (email, allow-listed, throttled) ----
def owner_emails() -> list[str]:
    return [e.strip().lower() for e in (os.environ.get("OWNER_ALERT_EMAILS") or "").split(",") if e.strip()]


async def owner_alert(store, key: str, subject: str, body: str, send=None) -> str:
    t = now()
    # tenant-safe: system-only — alert throttle rows
    prev = await store.ops_alerts.find_one({"key": key})
    if prev and prev.get("status") == "sent" and _aware(prev["sent_at"]) > t - ALERT_THROTTLE:
        await store.ops_alerts.update_one({"key": key}, {"$inc": {"suppressed": 1}})
        return "throttled"
    from app.kernel import email_channel as ec
    allow = ec._allowlist()
    to = [e for e in owner_emails() if ec.allowed(e, allow)]
    status = "no_recipient"
    if to and ec.email_enabled():
        send = send or ec.resend_send
        html = f"<p>{subject}</p><p>{body}</p><p>— brokAId Ops</p>"
        for addr in to:
            try:
                await send(to=addr, subject=f"[brokAId Ops] {subject}", html=html, text=f"{subject}\n\n{body}", from_name="brokAId Ops",
                           reply_to=None)
                status = "sent"
            except Exception as e:  # noqa: BLE001
                status = f"failed:{type(e).__name__}"
    elif to:
        status = "email_disabled"
    await store.ops_alerts.update_one({"key": key}, {"$set": {"key": key, "subject": subject, "sent_at": t, "status": status},
                                                     "$inc": {"count": 1}}, upsert=True)
    return status


def _aware(d):
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


async def watch(store, send=None) -> list[str]:
    """Owner alert conditions. Runs in every process (not via the scheduler, so a missing leader is still seen)."""
    t, fired = now(), []
    # tenant-safe: system-only — platform-level checks
    if await store.jobs.count_documents({"status": "dead", "finished_at": {"$lte": t - timedelta(hours=1)}}):
        fired.append(await owner_alert(store, "jobs:dead", "Dead background jobs for over an hour", "Open Ops → Jobs.", send))
    if await store.ops_requests.count_documents({"at": {"$gte": t - timedelta(minutes=15)}, "status": {"$gte": 500}}) >= ERROR_SPIKE:
        fired.append(await owner_alert(store, "error:spike", f"Error spike: {ERROR_SPIKE}+ server errors in 15 minutes", "Open Ops.", send))
    async for r in store.release_check_runs.find({"result": "FAIL", "started_at": {"$gte": t - timedelta(hours=24)}}).limit(1):
        fired.append(await owner_alert(store, f"release:{r.get('run_id')}", "Release check FAILED", "Open Governance → Release check.", send))
    s = await summary_spend(store)
    if s and s >= 80:
        fired.append(await owner_alert(store, "spend:80", f"AI spend at {s:.0f}% of the monthly cap", "Open Ops → Spend.", send))
    lead = await store["_locks"].find_one({"_id": "scheduler"})
    if lead and _aware(lead["expires_at"]) < t - timedelta(minutes=10):
        fired.append(await owner_alert(store, "jobs:leader", "Scheduler leader missing for 10+ minutes",
                                       "Background schedules (reminders, renewals, backups) are not running.", send))
    return fired


async def spend_total(store) -> float:
    """Estimated AI spend across tenants (the same estimate brAIn's cap uses)."""
    from app.kernel import spend
    total = 0.0
    async for tn in store.tenants.find({}, {"tenant_id": 1}):  # tenant-safe: system-only — tenant ids
        total += (await spend.summary(store, tn["tenant_id"]))["cost_usd"]
    return total


async def summary_spend(store) -> float | None:
    from app.kernel import spend
    cap = spend.cap_usd()
    return 100 * await spend_total(store) / cap if cap else None


async def watch_loop(store) -> None:
    while True:
        await asyncio.sleep(int(os.environ.get("OPS_WATCH_SECONDS") or 300))
        try:
            await watch(store)
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).exception("ops watch failed")
