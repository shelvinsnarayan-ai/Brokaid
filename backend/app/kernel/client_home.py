"""R3.1 — client app read models: Home (attention + cover rows with a big figure), the Activity feed, and the policy page.

Read-only aggregation over client-visible data. Never returns internal event names, staff notes, AI internals, other clients or
test data. Every query is tenant-scoped and limited to the session's client.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.db.tenant_safe import wrap
from app.kernel import holidays, payments
from app.kernel import renewals as rn
from app.kernel.tenant_ctx import use_tenant

CURRENT = ("managed", "in_force", "tracked")
PREVIOUS = ("renewed", "cancelled", "lapsed")
GROUPS = (("vehicles", "Vehicles"), ("business", "Business"), ("home", "Home & contents"), ("other", "Other"))
DOC_NAMES = {"FSG": "Financial Services Guide", "PDS": "Product Disclosure Statement", "TMD": "Target Market Determination",
             "RENEWAL_COMPARISON": "Renewal options (PDF)", "SCHEDULE": "Policy schedule", "PRIVACY": "Privacy policy",
             "COLLECTION_NOTICE": "Collection notice", "GAW": "General advice warning", "AFCA": "Complaints information"}
PAY_TEXT = {"owed": "Payment due", "partial": "Part payment received", "advised": "Payment advised", "received": "Payment received",
            "overpaid": "Payment received"}
PAY_STATE = {"owed": "Owed", "partial": "Part received", "advised": "Advised", "received": "Received", "overpaid": "Received"}
PAGE = 20


def utc(v) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


def money(n) -> str:
    n = float(n)
    if n >= 1_000_000:
        return f"A${n / 1_000_000:g}M"
    return f"A${n:,.0f}" if n == int(n) else f"A${n:,.2f}"


def group_of(p: dict) -> str:
    t = (p.get("product") or "").lower()
    if any(k in t for k in ("motor", "vehicle", "car", "ute", "truck")):
        return "vehicles"
    if any(k in t for k in ("liability", "business", "tools", "equipment", "marine", "cyber", "pack", "professional", "property")):
        return "business"
    if any(k in t for k in ("home", "contents", "building", "landlord", "strata")):
        return "home"
    return "other"


def _kind(p: dict) -> str:
    t = (p.get("product") or "").lower()
    if group_of(p) == "vehicles":
        return "motor"
    if "liability" in t or "cyber" in t or "professional" in t:
        return "liability"
    if any(k in t for k in ("tools", "equipment", "contents", "home", "building", "property", "pack")):
        return "property"
    return "other"


def _excess(s: dict):
    ex = s.get("excess") or {}
    if isinstance(ex, (int, float)):
        return ex
    return next((v for k, v in ex.items() if k != "currency" and isinstance(v, (int, float))), None)


def big_figure(p: dict, vehicle: dict | None = None) -> dict | None:
    """The headline number by type — never invented. Falls back to the excess, else None ("View details")."""
    s, kind = p.get("schedule") or {}, _kind(p)
    cts = s.get("cover_types") or []
    if kind == "motor":
        for key, label in (("agreed_value", "agreed value"), ("market_value", "market value")):
            v = (vehicle or {}).get(key)
            if isinstance(v, (int, float)) and v > 0:
                return {"text": money(v), "label": label, "kind": "value"}
    elif kind == "liability":
        v = next((c.get("limit_any_one") for c in cts if isinstance(c.get("limit_any_one"), (int, float))), None)
        v = v or max([x for x in (s.get("limits") or {}).values() if isinstance(x, (int, float))], default=None)
        if v:
            return {"text": money(v), "label": "limit", "kind": "value"}
    elif kind == "property":
        v = s.get("sum_insured") or next((c.get("sum_insured") for c in cts if isinstance(c.get("sum_insured"), (int, float))), None)
        if v:
            return {"text": money(v), "label": "sum insured", "kind": "value"}
    else:
        v = next((c.get("limit_any_one") or c.get("sum_insured") for c in cts if (c.get("limit_any_one") or c.get("sum_insured"))), None)
        if v:
            return {"text": money(v), "label": "limit", "kind": "value"}
    ex = _excess(s)
    return {"text": money(ex), "label": "excess", "kind": "excess"} if ex is not None else None


def _short(d) -> str:
    d = utc(d)
    return d.astimezone(holidays.SYD).strftime("%-d %b") if d else ""


def _long(d) -> str:
    d = utc(d)
    return d.astimezone(holidays.SYD).strftime("%-d %b %Y") if d else ""


def _days(d, now) -> int:
    return (utc(d).astimezone(holidays.SYD).date() - now.astimezone(holidays.SYD).date()).days


def _cover_type(p: dict) -> str:
    cts = (p.get("schedule") or {}).get("cover_types") or []
    return cts[0]["type"] if cts and cts[0].get("type") else (p.get("product") or "Policy")


def _renewal_note(p: dict, r: dict | None, now) -> tuple[str, str | None]:
    if p.get("status") == "tracked":
        return "Held elsewhere", None
    if r and rn.cur(r) not in rn.TERMINAL:
        d = _days(r["expiry"], now)
        return "Active", (f"Renewal due in {d} days" if d > 1 else "Renewal due today" if d == 0 else
                          "Renewal due tomorrow" if d == 1 else "Renewal overdue")
    return f"Active, renews {_short(p.get('expiry'))}", None


def cover_rows(policies: list[dict], renewals: dict, now) -> list[dict]:
    groups = {k: [] for k, _ in GROUPS}
    for p in policies:
        note, badge = _renewal_note(p, renewals.get(p["policy_id"]), now)
        base = {"policy_id": p["policy_id"], "group": group_of(p), "subtitle": f"{_cover_type(p)} · {note}", "badge": badge,
                "insurer": p.get("insurer_display")}
        vehicles = (p.get("schedule") or {}).get("vehicles") or []
        if base["group"] == "vehicles" and vehicles:
            for v in vehicles:
                title = " ".join(str(x) for x in (v.get("make"), v.get("model")) if x) or "Vehicle"
                groups["vehicles"].append({**base, "title": f"{title} · {v.get('rego') or ''}".strip(" ·"),
                                           "anchor": v.get("rego"), "figure": big_figure(p, v), "icon": "car"})
        else:
            groups[base["group"]].append({**base, "title": p.get("product") or "Policy", "anchor": None, "figure": big_figure(p),
                                          "icon": {"vehicles": "car", "business": "briefcase", "home": "home"}.get(base["group"], "shield")})
    return [{"key": k, "label": label, "rows": groups[k]} for k, label in GROUPS if groups[k]]


async def _ctx(store, tid, client_id):
    """Everything client-visible for one client, test data excluded. Caller has checked membership."""
    safe = wrap(store)
    async with use_tenant(tid):
        client = await safe.clients.find_one({"client_id": client_id}, {"_id": 0})
        if not client or client.get("test"):
            return None
        pols = [p async for p in safe.policies.find({"client_id": client_id, "test": {"$ne": True}}, {"_id": 0})]
        rens = [r async for r in safe.renewals.find({"client_id": client_id, "test": {"$ne": True}}, {"_id": 0})]
        pays = [p async for p in safe.payables.find({"client_id": client_id, "test": {"$ne": True}, "state": {"$ne": "void"}}, {"_id": 0})]
        claims = [c async for c in safe.claims.find({"client_id": client_id, "test": {"$ne": True}}, {"_id": 0})]
        docs = [d async for d in safe.doc_deliveries.find({"client_id": client_id}, {"_id": 0}).sort("at", -1).limit(200)]
        accs = [a async for a in safe.acceptances.find({"client_id": client_id}, {"_id": 0}).limit(200)]
    real = {p["policy_id"] for p in pols}
    return {"client": client, "policies": pols, "renewals": rens, "payables": [p for p in pays if p.get("policy_id") in real],
            "claims": claims, "docs": docs, "acceptances": accs}


def _latest_renewals(rens):
    out = {}
    for r in sorted(rens, key=lambda x: utc(x.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc)):
        out[r["policy_id"]] = r
    return out


def attention(ctx, unread: int, cert: dict | None) -> list[dict]:
    pols = {p["policy_id"]: p for p in ctx["policies"]}
    out = []
    for r in _latest_renewals(ctx["renewals"]).values():
        stage, cls = rn.cur(r), (pols.get(r["policy_id"]) or {}).get("product") or "your policy"
        if stage == "client_decision":
            out.append({"kind": "renewal_options", "title": "Renewal options ready", "subtitle": cls, "href": f"/c/renewal/{r['renewal_id']}"})
        elif stage == "paid":
            out.append({"kind": "acceptance_needed", "title": "Acceptance needed", "subtitle": f"{cls} renewal",
                        "href": f"/c/renewal/{r['renewal_id']}"})
    for p in ctx["payables"]:
        if payments.state_of(p) in ("owed", "partial"):
            due = _short(p.get("due_date"))
            out.append({"kind": "payment_due", "title": "Payment due", "subtitle": f"{money(p['amount'])}" + (f" by {due}" if due else ""),
                        "href": "/c/activity?f=payments"})
    for c in ctx["claims"]:
        if any(q.get("status") == "open" for q in c.get("info_requests") or []):
            out.append({"kind": "claim_document", "title": "Document requested", "subtitle": f"Claim {c['claim_reference']}",
                        "href": f"/c/claims/{c['claim_id']}"})
    if unread:
        out.append({"kind": "broker_replied", "title": "Your broker replied", "subtitle": f"{unread} new message{'s' if unread > 1 else ''}",
                    "href": "/c/messages"})
    if cert and cert.get("kind") == "certificate_ready" and cert.get("href"):
        out.append({"kind": "certificate_ready", "title": "Certificate ready", "subtitle": cert.get("summary") or "", "href": cert["href"]})
    return out


def activity_items(ctx, policy_id: str | None = None) -> list[dict]:
    pols = {p["policy_id"]: p for p in ctx["policies"]}
    name = lambda pid: (pols.get(pid) or {}).get("product") or "Policy"
    items = []
    for p in ctx["payables"]:
        st = payments.state_of(p)
        items.append({"type": "payments", "at": utc(p.get("advised_at") if st == "advised" else p.get("created_at")),
                      "title": PAY_TEXT[st], "subtitle": name(p["policy_id"]), "amount": money(p["amount"]), "state": PAY_STATE[st],
                      "crn": p.get("crn"), "policy_id": p["policy_id"], "href": "/c/activity?f=payments"})
    for c in ctx["claims"]:
        items.append({"type": "claims", "at": utc(c.get("lodged_at")), "title": f"Claim lodged · {c['claim_reference']}",
                      "subtitle": name(c.get("policy_id")), "policy_id": c.get("policy_id"), "href": f"/c/claims/{c['claim_id']}"})
        d = c.get("decision") or {}
        if d.get("at"):
            items.append({"type": "claims", "at": utc(d["at"]), "title": f"Claim decision · {c['claim_reference']}",
                          "subtitle": {"accepted": "Accepted", "declined": "Declined", "partial": "Partly accepted"}.get(d.get("outcome"), "Decision made"),
                          "policy_id": c.get("policy_id"), "href": f"/c/claims/{c['claim_id']}"})
    for r in ctx["renewals"]:
        stage = rn.cur(r)
        if stage in ("client_decision", "paid", "signed", "bound", "lapsed", "declined"):
            items.append({"type": "changes", "at": utc(r.get("updated_at") or r.get("created_at")), "title": "Renewal",
                          "subtitle": f"{name(r['policy_id'])} — {rn.CLIENT_TEXT[stage]}", "policy_id": r["policy_id"],
                          "href": f"/c/renewal/{r['renewal_id']}"})
    for a in ctx["acceptances"]:
        if a.get("kind") == "endorsement":
            items.append({"type": "changes", "at": utc(a.get("accepted_at") or a.get("created_at")), "title": "Change accepted",
                          "subtitle": name(a.get("policy_id")), "policy_id": a.get("policy_id"), "href": "/c/documents"})
    seen = set()
    for d in ctx["docs"]:
        key = (d.get("kind"), d.get("sha256"))
        if key in seen:
            continue
        seen.add(key)
        pid = (d.get("context") or "").split(":")[1] if (d.get("context") or "").startswith("policy:") else d.get("policy_id")
        items.append({"type": "documents", "at": utc(d.get("at")), "title": "Document delivered",
                      "subtitle": DOC_NAMES.get(d.get("kind"), "Document") + (f" · {d['insurer']}" if d.get("insurer") else ""),
                      "policy_id": pid, "href": "/c/documents"})
    if policy_id:
        items = [i for i in items if i.get("policy_id") == policy_id]
    items = [i for i in items if i["at"]]
    items.sort(key=lambda i: i["at"], reverse=True)
    for i in items:
        i["date"] = _long(i["at"])
        i["at"] = i["at"].isoformat()
    return items


def due_payments(ctx, settings, mode) -> list[dict]:
    pols = {p["policy_id"]: p for p in ctx["policies"]}
    out = []
    for p in ctx["payables"]:
        if payments.state_of(p) in ("owed", "partial"):
            d = payments.details(p, settings, mode)
            out.append({"payable_id": p["payable_id"], "policy": (pols.get(p["policy_id"]) or {}).get("product") or "Policy",
                        "amount": money(p["amount"]), "due": _long(p.get("due_date")), "crn": p["crn"], "bpay": d.get("bpay"),
                        "mocked": d.get("mocked", False), "message": d.get("message"),
                        "pay_href": f"/c/cover/{p['policy_id']}/pay/{p['envelope_id']}" if p.get("envelope_id") else None})
    return out


def policy_page(ctx, policy_id, now) -> dict | None:
    pols = {p["policy_id"]: p for p in ctx["policies"]}
    p = pols.get(policy_id)
    if not p or p.get("status") == "archived":
        return None
    s = p.get("schedule") or {}
    r = _latest_renewals(ctx["renewals"]).get(policy_id)
    inflight = r if r and rn.cur(r) not in rn.TERMINAL else None
    bullets = []
    for c in s.get("cover_types") or []:
        lim = c.get("limit_any_one") or c.get("sum_insured")
        bullets.append(f"{c.get('type') or 'Cover'}" + (f" — up to {money(lim)}" if lim else ""))
    if s.get("territorial_limits"):
        bullets.append(f"Where: {s['territorial_limits']}")
    ex = _excess(s)
    vehicles = [{"rego": v.get("rego"), "title": " ".join(str(x) for x in (v.get("year"), v.get("make"), v.get("model")) if x),
                 "use": v.get("use"), "figure": big_figure(p, v)} for v in s.get("vehicles") or []]
    prev = [{"policy_id": q["policy_id"], "product": q.get("product"), "period": f"{_long(q.get('inception'))} – {_long(q.get('expiry'))}",
             "status": q.get("status")} for q in ctx["policies"] if q.get("renewed_to") == policy_id or
            (q.get("status") in PREVIOUS and q.get("product") == p.get("product") and q["policy_id"] != policy_id)]
    status = ("Held elsewhere" if p.get("status") == "tracked" else
              f"Renewal: {rn.CLIENT_TEXT[rn.cur(inflight)]}" if inflight else f"Active until {_long(p.get('expiry'))}")
    return {"policy_id": policy_id, "title": p.get("product") or "Policy", "cover_type": _cover_type(p), "figure": big_figure(p),
            "insurer": p.get("insurer_display"), "status_text": status, "current": p.get("status") in CURRENT,
            "covered": bullets, "excess": money(ex) if ex is not None else None, "summary": s.get("summary"),
            "vehicles": vehicles, "renewal": {"renewal_id": inflight["renewal_id"], "text": rn.CLIENT_TEXT[rn.cur(inflight)]} if inflight else None,
            "activity": activity_items(ctx, policy_id)[:30], "previous": prev}
