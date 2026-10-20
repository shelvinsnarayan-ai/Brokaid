"""R2.3 — the brokerage's business details: validated, versioned (ledgered), and used to fill the draft compliance templates.

Values are what the brokerage enters. brokAId only checks formats (e.g. the ABN checksum); it never invents a licence number.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from app.db.tenant_safe import wrap
from app.kernel import compliance_templates as T
from app.kernel.holidays import SYD
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch

# key, label, plain-words help, max length
FIELDS = [
    ("legal_name", "Legal name", "The name on your ASIC registration, e.g. BurMac Insurance Brokers Pty Ltd.", 160),
    ("trading_name", "Trading name (optional)", "The name clients know you by, if different.", 160),
    ("abn", "ABN", "11 digits. We check the ABN checksum.", 20),
    ("acn", "ACN (optional)", "9 digits, if you are a company. We check the ACN checksum.", 20),
    ("licence_type", "Licence type", "Your own AFSL, or an authorised representative (AR) of another licensee.", 10),
    ("afsl_number", "AFSL number", "Only if you hold your own AFSL. Digits only, from ASIC's register.", 10),
    ("ar_number", "AR number", "Only if you are an authorised representative. Digits only, from ASIC's register.", 12),
    ("licensee_name", "Licensee name", "Only for an AR: the AFSL holder who authorises you.", 160),
    ("licensee_afsl", "Licensee AFSL number", "Only for an AR: your licensee's AFSL number.", 10),
    ("address", "Registered / business address", "Street address clients can write to.", 240),
    ("phone", "Phone", "An Australian number, e.g. 02 9000 0000 or 1300 000 000.", 20),
    ("email", "Email", "The address clients should use, e.g. info@yourbrokerage.com.au.", 160),
    ("website", "Website (optional)", "e.g. www.yourbrokerage.com.au", 160),
    ("classes", "Classes of insurance you arrange", "e.g. motor, property, liability, business packages, premium funding.", 400),
    ("commission_range", "Commission range (%)", "The range you usually receive from insurers, e.g. 0-25.", 20),
    ("broker_fees", "Broker fees", "What you charge and how it is calculated, e.g. a fee of $50 to $500 per policy.", 400),
    ("fees_when", "When fees are charged", "e.g. when a policy is arranged or renewed.", 200),
    ("other_benefits", "Other benefits (optional)", "Any other payments or benefits you or your staff receive, or 'none'.", 400),
    ("staff_pay", "How your staff are paid", "e.g. salary, plus bonuses not linked to the products clients choose.", 300),
    ("associations", "Associations and relationships", "Premium funders, referral arrangements, ownership links. Write 'none' if there are none.", 600),
    ("pi_insurer", "Professional indemnity insurer", "Who insures your professional indemnity (PI) cover.", 160),
    ("pi_statement", "Compensation statement (s912B)", "How your PI cover meets the compensation requirements, in your adviser's words.", 600),
    ("complaints_officer", "Complaints officer contact", "Name, phone, email and postal address.", 300),
    ("privacy_officer", "Privacy officer contact", "Name, phone and email.", 300),
    ("overseas", "Overseas disclosure countries", "Countries where data may be disclosed or stored, or 'none'.", 300),
]
KEYS = [f[0] for f in FIELDS]
NONE_OK = {"associations", "overseas", "other_benefits"}


class Invalid(Exception):
    def __init__(self, errors: dict[str, str]):
        super().__init__(errors)
        self.errors = errors


def abn_ok(v: str) -> bool:
    d = [int(c) for c in re.sub(r"\D", "", v)]
    if len(d) != 11:
        return False
    d[0] -= 1
    return sum(x * w for x, w in zip(d, (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19))) % 89 == 0


def acn_ok(v: str) -> bool:
    d = [int(c) for c in re.sub(r"\D", "", v)]
    return len(d) == 9 and (10 - sum(x * w for x, w in zip(d[:8], range(8, 0, -1))) % 10) % 10 == d[8]


def _digits(v: str, lo: int, hi: int) -> bool:
    return re.fullmatch(rf"\d{{{lo},{hi}}}", re.sub(r"\s", "", v)) is not None


def validate(body: dict) -> dict:
    """Clean every supplied field; empty fields stay blank (the drafts show them as remaining blanks)."""
    out, errs = {}, {}
    for key, label, _help, n in FIELDS:
        v = " ".join(str(body.get(key) or "").split()) if key not in ("address", "pi_statement", "associations") else str(body.get(key) or "").strip()
        if len(v) > n:
            errs[key] = f"{label} is too long (at most {n} characters)."
        out[key] = v
    lt = out["licence_type"].lower()
    if lt and lt not in ("afsl", "ar"):
        errs["licence_type"] = "Choose own AFSL or authorised representative."
    out["licence_type"] = lt
    checks = {"abn": (abn_ok, "That ABN doesn't pass the checksum. Check the 11 digits on the ABN Lookup."),
              "acn": (acn_ok, "That ACN doesn't pass the checksum. Check the 9 digits."),
              "afsl_number": (lambda v: _digits(v, 4, 7), "An AFSL number is 4 to 7 digits."),
              "licensee_afsl": (lambda v: _digits(v, 4, 7), "An AFSL number is 4 to 7 digits."),
              "ar_number": (lambda v: _digits(v, 4, 10), "An AR number is 4 to 10 digits."),
              "phone": (lambda v: re.fullmatch(r"(\+61|0|1[38]00|13)\d{6,9}", re.sub(r"[\s()-]", "", v)) is not None,
                        "Enter an Australian phone number, e.g. 02 9000 0000."),
              "email": (lambda v: re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", v, re.I) is not None, "Enter a valid email address."),
              "website": (lambda v: re.fullmatch(r"(https?://)?[\w.-]+\.[a-z]{2,}(/\S*)?", v, re.I) is not None, "Enter a web address, e.g. www.example.com.au."),
              "commission_range": (lambda v: _range(v) is not None, "Enter a range like 0-25 (percent, low to high, up to 100).")}
    for key, (ok, msg) in checks.items():
        if out[key] and not ok(out[key]):
            errs[key] = msg
    if lt == "afsl" and (out["ar_number"] or out["licensee_name"] or out["licensee_afsl"]):
        errs["ar_number"] = "Clear the AR fields when you hold your own AFSL."
    if lt == "ar" and out["afsl_number"]:
        errs["afsl_number"] = "Clear your own AFSL number when you are an authorised representative."
    if errs:
        raise Invalid(errs)
    for k in ("abn", "acn", "afsl_number", "ar_number", "licensee_afsl"):
        out[k] = re.sub(r"\D", "", out[k])
    return out


def _range(v: str):
    m = re.fullmatch(r"\s*(\d{1,3}(?:\.\d+)?)\s*%?\s*(?:-|–|to)\s*(\d{1,3}(?:\.\d+)?)\s*%?\s*", v)
    if not m or not 0 <= float(m.group(1)) <= float(m.group(2)) <= 100:
        return None
    return m.group(1), m.group(2)


def _abn_fmt(v: str) -> str:
    return f"{v[:2]} {v[2:5]} {v[5:8]} {v[8:]}"


def _none(v: str) -> bool:
    return v.strip().lower().rstrip(".") in ("none", "nil", "n/a")


def licence_line(d: dict) -> str | None:
    if d.get("licence_type") == "afsl" and d.get("afsl_number"):
        return f"AFSL {d['afsl_number']}"
    if d.get("licence_type") == "ar" and d.get("ar_number") and d.get("licensee_name") and d.get("licensee_afsl"):
        return f"Authorised Representative {d['ar_number']} of {d['licensee_name']} (AFSL {d['licensee_afsl']})"
    return None


def fill(d: dict) -> dict[str, str]:
    """Template placeholder key → filled text, for every key in compliance_templates.P that the details can answer."""
    g = lambda k: (d.get(k) or "").strip()  # noqa: E731
    out: dict[str, str] = {}
    if g("legal_name"):
        out["legal"] = g("legal_name") + (f" (trading as {g('trading_name')})" if g("trading_name") else "")
    if g("abn"):
        out["abn"] = _abn_fmt(g("abn")) + (f", ACN {g('acn')[:3]} {g('acn')[3:6]} {g('acn')[6:]}" if g("acn") else "")
    if lic := licence_line(d):
        out["licence"] = ("Australian Financial Services Licence (AFSL) No. " + g("afsl_number")) if g("licence_type") == "afsl" else lic
        out["licensee"] = (f"{g('legal_name')} (AFSL {g('afsl_number')})" if g("licence_type") == "afsl"
                           else f"{g('licensee_name')} (AFSL {g('licensee_afsl')})") if (g("legal_name") or g("licence_type") == "ar") else None
        if out["licensee"] is None:
            out.pop("licensee")
    if g("address"):
        out["address"] = g("address")
    if g("phone") and g("email"):
        out["contact"] = " · ".join(x for x in (f"Phone {g('phone')}", f"Email {g('email')}", f"Website {g('website')}" if g("website") else "") if x)
    if r := (_range(g("commission_range")) if g("commission_range") else None):
        out["commission"] = f"Our commission is generally between {r[0]}% and {r[1]}% of the base premium, depending on the class of insurance"
    if g("broker_fees"):
        out["fees"] = g("broker_fees").rstrip(".") + (f", charged {g('fees_when').rstrip('.')}" if g("fees_when") else "") + (
            "" if not g("other_benefits") or _none(g("other_benefits")) else f". Other benefits: {g('other_benefits').rstrip('.')}")
    if g("staff_pay"):
        out["staff_pay"] = g("staff_pay").rstrip(".")
    if g("associations"):
        out["associations"] = ("We have no associations or relationships that could reasonably be expected to influence our services"
                               if _none(g("associations")) else g("associations").rstrip("."))
    if g("pi_insurer") and g("pi_statement"):
        out["pi"] = f"We hold professional indemnity insurance with {g('pi_insurer')}. {g('pi_statement').rstrip('.')}"
    if g("complaints_officer"):
        out["complaints"] = g("complaints_officer")
    if g("privacy_officer"):
        out["privacy_officer"] = g("privacy_officer")
    if g("overseas"):
        out["overseas"] = "none — we do not disclose or store personal information overseas" if _none(g("overseas")) else g("overseas")
    if g("classes"):
        out["classes"] = "We arrange: " + g("classes").rstrip(".")
    if g("website") or g("email"):
        out["privacy_where"] = (f"Our Privacy Policy is available at {g('website')} or by asking us" if g("website")
                                else f"Ask us for a copy of our Privacy Policy at {g('email')}")
    return out


# The keys fill() can never answer are filled by the generator itself, per document version.
AUTO = {"date", "version"}
SOURCES = {"legal": ["legal_name"], "abn": ["abn"], "licence": ["licence_type", "afsl_number", "ar_number"],
           "licensee": ["licensee_name", "licensee_afsl"], "address": ["address"], "contact": ["phone", "email"],
           "commission": ["commission_range"], "fees": ["broker_fees"], "staff_pay": ["staff_pay"], "associations": ["associations"],
           "pi": ["pi_insurer", "pi_statement"], "complaints": ["complaints_officer"], "privacy_officer": ["privacy_officer"],
           "overseas": ["overseas"], "classes": ["classes"], "privacy_where": ["website", "email"]}
assert set(SOURCES) | AUTO == set(T.P), "every template placeholder must map to business details or be auto-filled"


def label_for(key: str) -> str:
    names = dict((k, lab) for k, lab, *_ in FIELDS)
    return "Automatic" if key in AUTO else " / ".join(names[f] for f in SOURCES[key])


async def current(store, tid: str) -> dict:
    async with use_tenant(tid):
        row = await wrap(store).business_details.find_one({}, {"_id": 0}, sort=[("version", -1)])
    return row or {"version": 0, "fields": {k: "" for k in KEYS}}


async def save(store, tid: str, body: dict, *, by: str, role: str, reason: str = "edit") -> dict:
    clean = validate(body)
    prev = await current(store, tid)
    changed = [k for k in KEYS if clean.get(k, "") != prev["fields"].get(k, "")]
    if not changed:
        return prev
    v = prev["version"] + 1
    sha = hashlib.sha256(json.dumps(clean, sort_keys=True).encode()).hexdigest()
    row = {"version": v, "fields": clean, "sha256": sha, "changed": changed, "by": by, "at": datetime.now(timezone.utc)}
    safe = wrap(store)
    async with use_tenant(tid):
        await safe.business_details.insert_one(dict(row))
        await safe.system("tenants").update_one({"tenant_id": tid}, {"$set": {  # the tenant record feeds the AFSL/AR line elsewhere
            "legal_name": clean["legal_name"] or None, "trading_name": clean["trading_name"] or None, "abn": clean["abn"] or None,
            "acn": clean["acn"] or None, "afsl": clean["afsl_number"] if clean["licence_type"] == "afsl" and clean["afsl_number"] else None,
            "ar_number": licence_line(clean) if clean["licence_type"] == "ar" else None}})
        await append_via_dispatch(store, tid, "BusinessDetailsUpdated", actor={"kind": "human", "id": by, "name": role},
                                  payload={"version": v, "changed": changed, "sha256": sha, "reason": reason}, idempotency_key=f"business_details:{v}")
    row.pop("_id", None)
    return row


def today() -> str:
    return f"{datetime.now(timezone.utc).astimezone(SYD):%d/%m/%Y}"
