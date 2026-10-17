"""R1.4 — mapping-based iAdviser import: parse, suggest, validate, match, diff, commit.

Rows are validated into `import_rows` (normalised values only — never logged). Commit writes
through ledger events under new tenant-local ids. Re-importing the same file is a no-op.
"""
from __future__ import annotations

import csv
import difflib
import hashlib
import io
import json
import re
import uuid
from datetime import date, datetime, timedelta, timezone

from app.db.tenant_safe import wrap
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch
from app.kernel.trail_mongo import DuplicateIdempotencyKey

MAX_BYTES = 20 * 1024 * 1024
ORDER = ("clients", "insurers", "policies", "vehicles", "contacts")
R, O = True, False
FIELDS: dict[str, dict[str, tuple[bool, tuple[str, ...]]]] = {
    "clients": {
        "client_code": (R, ("client code", "client id", "client no", "client number", "client ref", "account code", "iadviser code", "code")),
        "name": (R, ("client name", "insured name", "insured", "entity name", "business name", "company name", "name")),
        "abn": (O, ("abn", "a b n", "australian business number")),
        "email": (O, ("email", "email address", "e mail")),
        "phone": (O, ("phone", "telephone", "phone number", "mobile", "business phone")),
        "address": (O, ("address", "street address", "postal address", "business address")),
        "state": (O, ("state",)),
        "postcode": (O, ("postcode", "post code")),
        "industry": (O, ("industry", "occupation", "business description", "anzsic description")),
        "account_manager": (O, ("account manager", "account executive", "servicing broker", "handler", "ae")),
    },
    "insurers": {
        "insurer_code": (R, ("insurer code", "underwriter code", "code")),
        "name": (R, ("insurer name", "underwriter name", "insurer", "underwriter", "name")),
        "abn": (O, ("abn",)),
    },
    "policies": {
        "policy_number": (R, ("policy no", "policy number", "policy num", "policy ref", "policy")),
        "client_code": (R, ("client code", "client id", "client no", "client ref", "account code")),
        "insurer": (O, ("insurer underwriter", "insurer", "underwriter", "insurer name", "insurer code")),
        "class": (R, ("class", "class of business", "policy class", "policy type", "product", "cover type")),
        "inception": (R, ("inception", "inception date", "start date", "effective date", "period from")),
        "expiry": (R, ("expiry", "expiry date", "end date", "renewal date", "period to")),
        "base_premium": (O, ("base premium", "premium", "annual premium")),
        "total_premium": (O, ("total premium", "total payable", "gross premium")),
        "sum_insured": (O, ("sum insured", "limit of indemnity", "limit", "si")),
        "excess": (O, ("excess", "deductible")),
        "status": (O, ("status", "policy status")),
    },
    "vehicles": {
        "policy_number": (R, ("policy no", "policy number", "policy ref", "policy")),
        "rego": (R, ("rego", "registration", "registration no", "rego no", "plate", "registration number")),
        "vin": (O, ("vin", "chassis", "vin chassis", "chassis no")),
        "make": (O, ("make",)),
        "model": (O, ("model",)),
        "year": (O, ("year", "year of manufacture", "yom", "build year")),
        "sum_insured": (O, ("sum insured", "agreed value", "market value", "value")),
        "use": (O, ("use", "usage", "vehicle use")),
        "garaged_address": (O, ("garaged address", "garaging address", "garaged")),
    },
    "contacts": {
        "client_code": (R, ("client code", "client id", "client no", "client ref")),
        "name": (R, ("contact name", "contact", "name", "full name")),
        "email": (O, ("email", "email address")),
        "phone": (O, ("phone", "mobile", "telephone")),
        "role": (O, ("role", "position", "title")),
    },
}
SHEET_HINTS = (("client", "clients"), ("insurer", "insurers"), ("underwriter", "insurers"), ("polic", "policies"),
               ("vehicle", "vehicles"), ("asset", "vehicles"), ("motor", "vehicles"), ("contact", "contacts"))


def _n(s) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(s or "").lower())).strip()


# ---- parsing -------------------------------------------------------------
def parse(data: bytes, filename: str) -> list[dict]:
    """→ [{name, headers, rows}] — rows are lists aligned to headers (row 1 = headers)."""
    if filename.lower().endswith((".xlsx", ".xlsm")):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        sheets = [(ws.title, [list(r) for r in ws.iter_rows(values_only=True)]) for ws in wb.worksheets]
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1252")
        sheets = [(filename.rsplit(".", 1)[0], list(csv.reader(io.StringIO(text))))]
    out = []
    for name, rows in sheets:
        rows = [r for r in rows if any(c not in (None, "") for c in r)]
        if not rows:
            continue
        headers = [str(h).strip() if h is not None else "" for h in rows[0]]
        out.append({"name": name, "headers": headers, "rows": [r + [None] * (len(headers) - len(r)) for r in rows[1:]]})
    return out


def suggest(headers: list[str], entity: str, profile: dict | None = None) -> dict[str, str | None]:
    """field → header. A saved profile wins for headers present in the file."""
    norm = {_n(h): h for h in headers if h}
    taken: set[str] = set()
    out: dict[str, str | None] = {}
    saved = (profile or {}).get(entity) or {}
    for field in FIELDS[entity]:
        if saved.get(field) in headers:
            out[field] = saved[field]
            taken.add(saved[field])
    for exact in (True, False):
        for field, (_, syns) in FIELDS[entity].items():
            if out.get(field):
                continue
            for s in syns:
                cands = [norm[s]] if exact and s in norm else ([] if exact else
                         [norm[m] for m in difflib.get_close_matches(s, list(norm), n=1, cutoff=0.86)])
                cands = [c for c in cands if c not in taken]
                if cands:
                    out[field] = cands[0]
                    taken.add(cands[0])
                    break
    return {f: out.get(f) for f in FIELDS[entity]}


def detect_entity(sheet_name: str, headers: list[str]) -> str:
    sn = _n(sheet_name)
    for hint, ent in SHEET_HINTS:
        if hint in sn:
            return ent

    def score(ent):
        m = suggest(headers, ent)
        return sum(2 if FIELDS[ent][f][0] else 1 for f, h in m.items() if h)
    return max(ORDER, key=score)


# ---- value checks ---------------------------------------------------------
class Bad(ValueError):
    pass


def abn(v) -> str:
    d = re.sub(r"\D", "", str(v))
    if len(d) != 11:
        raise Bad(f"ABN '{v}' must have 11 digits")
    w = (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)
    if sum((int(c) - (1 if i == 0 else 0)) * w[i] for i, c in enumerate(d)) % 89:
        raise Bad(f"ABN '{v}' fails the ABN checksum (check for a typo)")
    return d


def a_date(v) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)) and 20000 < v < 80000:  # Excel serial
        return date(1899, 12, 30) + timedelta(days=int(v))
    s = str(v).strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise Bad(f"Date '{s}' isn't dd/mm/yyyy or yyyy-mm-dd")


def money(v) -> float:
    if isinstance(v, (int, float)):
        f = float(v)
    else:
        s = str(v).strip().replace("AUD", "").replace("$", "").replace(",", "").replace(" ", "")
        neg = s.startswith("(") and s.endswith(")")
        try:
            f = float(s.strip("()")) * (-1 if neg else 1)
        except ValueError:
            raise Bad(f"Amount '{v}' isn't a number") from None
    if f < 0:
        raise Bad(f"Amount '{v}' can't be negative")
    return round(f, 2)


def rego(v) -> str:
    r = re.sub(r"[\s\-]", "", str(v)).upper()
    if not re.fullmatch(r"[A-Z0-9]{2,7}", r):
        raise Bad(f"Rego '{v}' isn't a valid Australian plate (2–7 letters or numbers)")
    return r


def _blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _txt(v) -> str:
    return str(int(v)) if isinstance(v, float) and v.is_integer() else str(v).strip()


def normalise(entity: str, raw: dict) -> tuple[dict, list[dict]]:
    """raw field→cell value → (record, issues[{level, text}])."""
    rec: dict = {}
    iss: list[dict] = []

    def err(t):
        iss.append({"level": "error", "text": t})

    def warn(t):
        iss.append({"level": "warn", "text": t})
    for f, (req, _) in FIELDS[entity].items():
        v = raw.get(f)
        if _blank(v):
            if req:
                err(f"Missing {f.replace('_', ' ')}")
            continue
        try:
            if f == "abn":
                rec[f] = abn(v)
            elif f in ("inception", "expiry"):
                rec[f] = a_date(v).isoformat()
            elif f in ("base_premium", "total_premium", "sum_insured", "excess"):
                rec[f] = money(v)
            elif f == "rego":
                rec[f] = rego(v)
            elif f == "vin":
                rec[f] = _txt(v).upper().replace(" ", "")
                if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", rec[f]):
                    warn("VIN should be 17 characters without I, O or Q")
            elif f == "year":
                y = int(float(_txt(v)))
                if not 1950 <= y <= date.today().year + 1:
                    warn(f"Year {y} looks wrong")
                rec[f] = y
            elif f == "email":
                rec[f] = _txt(v).lower()
                if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", rec[f]):
                    warn(f"Email '{rec[f]}' doesn't look like an email address")
            elif f in ("client_code", "policy_number", "insurer_code"):
                rec[f] = _txt(v).upper()
            else:
                rec[f] = _txt(v)
        except (Bad, ValueError) as e:
            err(str(e) if isinstance(e, Bad) else f"{f.replace('_', ' ').capitalize()} '{v}' isn't valid")
    if entity == "policies":
        if rec.get("inception") and rec.get("expiry") and rec["expiry"] < rec["inception"]:
            err("Expiry is before inception")
        if "base_premium" not in rec and "total_premium" not in rec:
            warn("No premium given")
    return rec, iss


def fields_hash(rec: dict) -> str:
    return hashlib.sha256(json.dumps(rec, sort_keys=True, default=str).encode()).hexdigest()


# ---- validate + match ------------------------------------------------------
async def _existing(safe) -> dict[str, dict]:
    out: dict[str, dict] = {e: {} for e in ORDER}
    async for c in safe.clients.find({"external.iadviser_client_code": {"$exists": True}, "archived_at": None}):
        out["clients"][c["external"]["iadviser_client_code"]] = c
        for ct in c.get("contacts") or []:
            out["contacts"][f"{c['external']['iadviser_client_code']}|{_n(ct.get('name'))}"] = {"import": ct.get("import") or {}}
    async for i in safe.insurers.find({}):
        out["insurers"][i["insurer_code"]] = i
    async for p in safe.policies.find({"external.iadviser_policy_number": {"$exists": True}, "archived_at": None}):
        out["policies"][p["external"]["iadviser_policy_number"]] = p
    async for t in safe.things.find({"external.iadviser_vehicle_key": {"$exists": True}, "archived_at": None}):
        out["vehicles"][t["external"]["iadviser_vehicle_key"]] = t
    return out


def _key(entity: str, rec: dict) -> str | None:
    if entity == "vehicles":
        return f"{rec['policy_number']}|{rec['rego']}" if rec.get("policy_number") and rec.get("rego") else None
    if entity == "contacts":
        return f"{rec['client_code']}|{_n(rec['name'])}" if rec.get("client_code") and rec.get("name") else None
    k = {"clients": "client_code", "insurers": "insurer_code", "policies": "policy_number"}[entity]
    return rec.get(k)


async def staff_lookup(safe) -> dict[str, str]:
    out = {}
    async for u in safe.users.find({"kind": "operator", "disabled": {"$ne": True}}):
        for k in (u.get("email"), u.get("name"), u.get("display_name")):
            if k:
                out[_n(k)] = u["user_id"]
    return out


async def validate(store, tenant_id: str, *, run_id: str, file_sha: str, sheets: list[dict]) -> dict:
    """sheets: [{name, entity, mapping, headers, rows}] → writes import_rows, returns counts."""
    safe = wrap(store)
    async with use_tenant(tenant_id):
        have = await _existing(safe)
        staff = await staff_lookup(safe)
        seen: dict[str, dict[str, int]] = {e: {} for e in ORDER}
        ok_keys: dict[str, set] = {e: set() for e in ORDER}
        docs = []
        for sh in sorted(sheets, key=lambda s: ORDER.index(s["entity"])):
            ent, mp = sh["entity"], sh["mapping"]
            idx = {f: sh["headers"].index(h) for f, h in mp.items() if h and h in sh["headers"]}
            for i, row in enumerate(sh["rows"], start=2):
                rec, iss = normalise(ent, {f: row[j] for f, j in idx.items()})
                key = _key(ent, rec)
                if key and key in seen[ent]:
                    iss.append({"level": "error", "text": f"Duplicate: {key.split('|')[0] if ent != 'vehicles' else key.replace('|', ' / ')} "
                                f"already appears on row {seen[ent][key]} of this file"})
                elif key:
                    seen[ent][key] = i
                if ent in ("policies", "contacts") and rec.get("client_code") and \
                        rec["client_code"] not in ok_keys["clients"] and rec["client_code"] not in have["clients"]:
                    iss.append({"level": "error", "text": f"No client with code {rec['client_code']} in this file or already imported"})
                if ent == "vehicles" and rec.get("policy_number") and \
                        rec["policy_number"] not in ok_keys["policies"] and rec["policy_number"] not in have["policies"]:
                    iss.append({"level": "error", "text": f"No policy {rec['policy_number']} in this file or already imported"})
                if ent == "clients" and rec.get("account_manager") and _n(rec["account_manager"]) not in staff:
                    iss.append({"level": "warn", "text": f"Account manager '{rec['account_manager']}' isn't on your staff list; assign later"})
                status = "error" if any(x["level"] == "error" for x in iss) else "warn" if iss else "ok"
                action, diff = None, {}
                if status != "error":
                    ok_keys[ent].add(key)
                    prev = (have[ent].get(key) or {}).get("import") or {}
                    if not prev:
                        action = "create"
                    elif prev.get("hash") == fields_hash(rec):
                        action = "unchanged"
                    else:
                        old = prev.get("fields") or {}
                        diff = {f: [old.get(f), rec.get(f)] for f in sorted(set(old) | set(rec)) if old.get(f) != rec.get(f)}
                        action = "update"
                docs.append({"row_id": str(uuid.uuid4()), "run_id": run_id, "sheet": sh["name"], "row": i, "entity": ent,
                             "order": ORDER.index(ent), "key": key, "status": status, "issues": iss, "action": action,
                             "diff": diff, "fields": rec, "committed": False, "record_id": None})
        for k in range(0, len(docs), 1000):
            await safe.import_rows.insert_many(docs[k:k + 1000])
    return counts(docs)


def counts(rows: list[dict]) -> dict:
    c: dict = {}
    for r in rows:
        e = c.setdefault(r["entity"], {"rows": 0, "ok": 0, "warn": 0, "error": 0, "create": 0, "update": 0, "unchanged": 0})
        e["rows"] += 1
        e[r["status"]] += 1
        if r["action"]:
            e[r["action"]] += 1
    return c


# ---- commit ------------------------------------------------------------------
def _dt(iso: str | None):
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc) if iso else None


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


async def _event(store, tid, typ, payload, idem, actor):
    try:
        await append_via_dispatch(store, tid, typ, actor=actor, payload=payload, idempotency_key=idem)
    except DuplicateIdempotencyKey:
        pass  # resumed after a crash between ledger write and row mark


async def commit_row(store, tid: str, run: dict, row: dict, staff: dict[str, str]) -> str | None:
    """Apply one validated row. Caller holds use_tenant(tid). Returns the record id."""
    safe, rec, ent = wrap(store), row["fields"], row["entity"]
    now = datetime.now(timezone.utc)
    basis = f"iAdviser import {run['file_sha'][:12]}, {row['sheet']} row {row['row']}"
    imp = {"fields": rec, "hash": fields_hash(rec), "file_sha": run["file_sha"], "row": row["row"], "run_id": run["run_id"]}
    actor = {"kind": "human", "id": run["created_by"], "name": "import"}
    idem = f"imp:{run['run_id']}:{row['row_id']}"
    upd = row["action"] == "update"
    changed = sorted(row.get("diff") or {})
    if ent == "clients":
        prev = await safe.clients.find_one({"external.iadviser_client_code": rec["client_code"], "archived_at": None})
        cid = prev["client_id"] if prev else str(uuid.uuid4())
        body = {"display_name": rec["name"], "abn": rec.get("abn"), "email": rec.get("email"), "phone": rec.get("phone"),
                "address": rec.get("address"), "industry": rec.get("industry") or "", "jurisdiction_code": "AU",
                "domicile": ", ".join(x for x in (rec.get("state"), "AU") if x), "import": imp}
        ae = staff.get(_n(rec.get("account_manager")))
        if ae:
            body["assigned_ae_user_id"] = ae
        if prev:
            await safe.clients.update_one({"client_id": cid}, {"$set": body})
        else:
            await safe.clients.insert_one({"client_id": cid, "external": {"iadviser_client_code": rec["client_code"]},
                                           "contacts": [], "archived_at": None, "created_at": now, **body})
        await _event(store, tid, "ClientUpdated" if upd else "ClientImported",
                     {"client_id": cid, "client_code": rec["client_code"], "changed": changed, "data_basis": basis}, idem, actor)
        return cid
    if ent == "insurers":
        await safe.insurers.update_one({"insurer_code": rec["insurer_code"]},
                                       {"$set": {"name": rec["name"], "abn": rec.get("abn"), "import": imp}}, upsert=True)
        await _event(store, tid, "InsurerImported", {"insurer_code": rec["insurer_code"], "data_basis": basis}, idem, actor)
        return rec["insurer_code"]
    if ent == "contacts":
        client = await safe.clients.find_one({"external.iadviser_client_code": rec["client_code"], "archived_at": None})
        contacts = [c for c in client.get("contacts") or [] if _n(c.get("name")) != _n(rec["name"])]
        contacts.append({"name": rec["name"], "email": rec.get("email"), "phone": rec.get("phone"), "role": rec.get("role"),
                         "login": None, "import": imp})
        await safe.clients.update_one({"client_id": client["client_id"]}, {"$set": {"contacts": contacts}})
        await _event(store, tid, "ClientUpdated", {"client_id": client["client_id"], "changed": ["contacts"], "data_basis": basis},
                     idem, actor)
        return client["client_id"]
    if ent == "policies":
        client = await safe.clients.find_one({"external.iadviser_client_code": rec["client_code"], "archived_at": None})
        prev = await safe.policies.find_one({"external.iadviser_policy_number": rec["policy_number"], "archived_at": None})
        ins = await safe.insurers.find_one({"insurer_code": (rec.get("insurer") or "").upper()})
        pid = prev["policy_id"] if prev else str(uuid.uuid4())
        si, cls = rec.get("sum_insured"), rec["class"]
        sched = dict((prev or {}).get("schedule") or {"vehicles": [], "addresses": [a for a in [client.get("address")] if a]})
        sched["cover_types"] = [{"type": cls, "limit_any_one": si, "currency": "AUD"}]
        sched["limits"] = {_slug(cls): si} if si is not None else {}
        if rec.get("excess") is not None:
            sched["excess"] = {"any_one_occurrence": rec["excess"], "currency": "AUD"}
        lapsed = _n(rec.get("status")) in ("lapsed", "cancelled", "canceled", "expired")
        body = {"client_id": client["client_id"], "product": cls, "insurer_display": (ins or {}).get("name") or rec.get("insurer") or "",
                "inception": _dt(rec["inception"]), "expiry": _dt(rec["expiry"]), "status": "lapsed" if lapsed else "managed",
                "premium_amount": rec.get("total_premium", rec.get("base_premium")), "base_premium": rec.get("base_premium"),
                "premium_currency": "AUD", "premium_status": "current", "schedule": sched, "import": imp,
                "imported_from": {"source": "iadviser", "file_sha": run["file_sha"], "row": row["row"]}}
        if prev:
            await safe.policies.update_one({"policy_id": pid}, {"$set": body})
        else:
            await safe.policies.insert_one({"policy_id": pid, "external": {"iadviser_policy_number": rec["policy_number"]},
                                            "archived_at": None, "created_at": now, **body})
        await _event(store, tid, "PolicyUpdated" if upd else "PolicyImported",
                     {"policy_id": pid, "client_id": client["client_id"], "policy_number": rec["policy_number"],
                      "changed": changed, "data_basis": basis}, idem, actor)
        return pid
    # vehicles → policy schedule + a Thing
    pol = await safe.policies.find_one({"external.iadviser_policy_number": rec["policy_number"], "archived_at": None})
    veh = {"rego": rec["rego"], "vin": rec.get("vin"), "make": rec.get("make"), "model": rec.get("model"), "year": rec.get("year"),
           "agreed_value": rec.get("sum_insured"), "use": rec.get("use"), "garaged_address": rec.get("garaged_address")}
    sched = dict(pol.get("schedule") or {})
    sched["vehicles"] = [v for v in sched.get("vehicles") or [] if v.get("rego") != rec["rego"]] + [veh]
    await safe.policies.update_one({"policy_id": pol["policy_id"]}, {"$set": {"schedule": sched}})
    key = row["key"]
    prev = await safe.things.find_one({"external.iadviser_vehicle_key": key, "archived_at": None})
    tid_ = prev["thing_id"] if prev else f"thing-imp-{uuid.uuid4().hex[:12]}"
    thing = {"client_id": pol["client_id"], "group": "vehicle", "status": "covered", "template": "motor",
             "name": " ".join(str(x) for x in (rec.get("make"), rec.get("model")) if x) or rec["rego"],
             "identifiers": {"rego": rec["rego"], "vin": rec.get("vin")},
             "template_fields": {"cover_level": pol.get("product") or "Motor", "who_can_drive": "As shown on your policy schedule",
                                 "restriction": f"{rec.get('use') or 'business'} use"},
             "schedule_items": [{"policy_id": pol["policy_id"], "section": "vehicles", "ref": rec["rego"],
                                 "fields": {**{k: veh[k] for k in ("make", "model", "year", "agreed_value", "use") if veh.get(k) is not None},
                                            **({"excess": sched["excess"]["any_one_occurrence"]} if sched.get("excess") else {})}}],
             "import": imp, "provenance": {"source": "iadviser import", "file_sha": run["file_sha"], "row": row["row"], "at": now.isoformat()}}
    if prev:
        await safe.things.update_one({"thing_id": tid_}, {"$set": thing})
    else:
        await safe.things.insert_one({"thing_id": tid_, "external": {"iadviser_vehicle_key": key},
                                      "worth_knowing": [], "documents": [], "interested_parties": [], "told_us_on": None,
                                      "archived_at": None, **thing})
    await _event(store, tid, "PolicyUpdated", {"policy_id": pol["policy_id"], "client_id": pol["client_id"],
                                               "changed": ["schedule.vehicles"], "vehicle": "updated" if upd else "added",
                                               "data_basis": basis}, idem, actor)
    return tid_


async def commit_run(store, tid: str, run_id: str, *, batch: int = 200) -> dict:
    """Resumable: rows already marked committed are skipped; progress is saved on the run."""
    safe = wrap(store)
    async with use_tenant(tid):
        run = await safe.import_runs.find_one({"run_id": run_id})
        staff = await staff_lookup(safe)
        q = {"run_id": run_id, "status": {"$ne": "error"}, "action": {"$in": ["create", "update"]}}
        total = await safe.import_rows.count_documents(q)
        while True:
            rows = [r async for r in safe.import_rows.find({**q, "committed": False}).sort([("order", 1), ("row", 1)]).limit(batch)]
            if not rows:
                break
            for r in rows:
                rid = await commit_row(store, tid, run, r, staff)
                await safe.import_rows.update_one({"row_id": r["row_id"]}, {"$set": {"committed": True, "record_id": rid}})
            done = total - await safe.import_rows.count_documents({**q, "committed": False})
            await safe.import_runs.update_one({"run_id": run_id}, {"$set": {"progress": {"done": done, "total": total}}})
        await safe.import_runs.update_one({"run_id": run_id}, {"$set": {
            "status": "committed", "committed_at": datetime.now(timezone.utc), "progress": {"done": total, "total": total}}})
        if total:
          await _event(store, tid, "ImportCommitted", {"run_id": run_id, "file_sha": run["file_sha"], "rows_written": total,
                                                     "counts": run.get("counts")}, f"imp:{run_id}:done",
                     {"kind": "human", "id": run["created_by"], "name": "import"})
    return {"written": total}
