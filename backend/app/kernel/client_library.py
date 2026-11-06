"""R3.12 J7 — client Documents grouped by policy. Internal/engine paperwork never leaves the server."""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote

from app.db.tenant_safe import wrap
from app.kernel.tenant_ctx import use_tenant

CLIENT_KINDS = {"FSG", "PDS", "TMD", "WORDING", "RENEWAL_COMPARISON"}
# Shown in the report: kinds that are hidden from clients (or moved) server-side.
HIDDEN_KINDS = ("BINDING", "BINDER", "CHANGE_PACKET", "SYSTEM", "ENGINE", "INTERNAL", "HASH_MANIFEST", "OPERATOR_APPROVAL")
GROUPS = (("schedule", "Current schedule"), ("previous", "Previous schedules"), ("pds", "PDS and policy wording"),
          ("certificates", "Certificates"), ("receipts", "Receipts and signed acceptances"), ("letters", "Letters and other"))
NAMES = {"FSG": "Financial Services Guide", "PDS": "Product Disclosure Statement", "TMD": "Target Market Determination",
         "WORDING": "Policy wording", "RENEWAL_COMPARISON": "Renewal options comparison"}


def client_visible(row: dict) -> bool:
    return (row.get("kind") in CLIENT_KINDS and not row.get("staff_only") and not row.get("internal")
            and not row.get("test"))


def _ask(text: str) -> str:
    return f"/c/messages/broker?draft={quote(text)}"


def _long(d) -> str:
    if isinstance(d, str):
        d = datetime.fromisoformat(d)
    if not d:
        return ""
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).strftime("%-d %b %Y")


async def library(store, tid: str, client_id: str) -> dict:
    safe = wrap(store)
    async with use_tenant(tid):
        pols = [p async for p in safe.policies.find({"client_id": client_id, "test": {"$ne": True},
                                                      "status": {"$in": ["managed", "in_force"]}}, {"_id": 0})]
        files = [f async for f in safe.policy_documents.find({"client_id": client_id, "staff_only": {"$ne": True}, "withdrawn_at": None}, {"_id": 0})]
        dels = [d async for d in safe.doc_deliveries.find({"client_id": client_id}, {"_id": 0}).sort("at", -1)]
        accs = [a async for a in safe.acceptances.find({"client_id": client_id}, {"_id": 0})]
        certs = {e["payload"].get("policy_id") async for e in safe.events.find(
            {"type": "CertificateRegenerated", "payload.policy_id": {"$in": [p["policy_id"] for p in pols]}}, {"payload.policy_id": 1})}
    dels = [d for d in dels if client_visible(d)]
    out = []
    for p in sorted(pols, key=lambda p: p.get("product") or ""):
        g, used = {k: [] for k, _ in GROUPS}, set()
        for f in files:
            if f["policy_id"] != p["policy_id"]:
                continue
            item = {"id": f["doc_id"], "title": f["title"], "subtitle": f.get("term_label") or "", "sample": bool(f.get("sample")),
                    "href": f"/api/c/documents/file/{f['doc_id']}", "download": True}
            key = {"schedule": "schedule" if f.get("current") else "previous", "pds": "pds", "certificate": "certificates",
                   "receipt": "receipts"}.get(f["kind"], "letters")
            g[key].append(item)
        for d in dels:
            if d.get("insurer") and d["insurer"] == p.get("insurer_display") and d["kind"] in ("PDS", "WORDING", "TMD"):
                key = (d["kind"], d.get("insurer"), d.get("version"))
                if key in used:
                    continue
                used.add(key)
                g["pds" if d["kind"] != "TMD" else "letters"].append(
                    {"id": d["delivery_id"], "title": NAMES[d["kind"]], "subtitle": f"{d['insurer']} · version {d.get('version') or ''}".strip(),
                     "href": f"/api/c/documents/download?d={d['delivery_id']}", "download": True})
            elif (d.get("context") or "").endswith(p["policy_id"]) and d["kind"] == "RENEWAL_COMPARISON":
                g["letters"].append({"id": d["delivery_id"], "title": NAMES[d["kind"]], "subtitle": _long(d.get("at")),
                                     "href": f"/api/c/documents/download?d={d['delivery_id']}", "download": True})
        if p["policy_id"] in certs:
            g["certificates"].append({"id": f"cert-{p['policy_id']}", "title": "Certificate of currency", "subtitle": "Latest",
                                      "href": f"/c/cover/{p['policy_id']}/certificate", "download": False})
        seen = set()
        for a in accs:
            if a.get("policy_id") == p["policy_id"] and a.get("kind") in ("renewal", "endorsement") and a.get("ref_id") not in seen:
                seen.add(a.get("ref_id"))
                g["receipts"].append({"id": a["acceptance_id"], "title": f"Signed acceptance · {a['kind']}",
                                      "subtitle": " · ".join(x for x in (_long(a.get("accepted_at") or a.get("created_at")), a.get("label")) if x), "href": f"/c/acceptance/{a['kind']}/{a['ref_id']}", "download": False})
        name = p.get("product") or "policy"
        missing = {"schedule": not g["schedule"], "pds": not g["pds"]}
        out.append({"policy_id": p["policy_id"], "title": name, "insurer": p.get("insurer_display"),
                    "groups": [{"key": k, "label": label, "items": g[k]} for k, label in GROUPS],
                    "missing": missing,
                    "ask_schedule": _ask(f"Hi, could you send me a copy of the current schedule for my {name} policy?") if missing["schedule"] else None,
                    "ask_pds": _ask(f"Hi, could you send me the PDS and policy wording for my {name} policy?") if missing["pds"] else None})
    general = [{"id": d["delivery_id"], "title": NAMES[d["kind"]], "subtitle": " · ".join(x for x in (d.get("insurer"), _long(d.get("at"))) if x),
                "href": f"/api/c/documents/download?d={d['delivery_id']}", "download": True}
               for d in dels if d["kind"] == "FSG"]
    return {"policies": out, "general": general[:3]}


async def file_for(store, tid: str, client_ids: set, doc_id: str) -> dict | None:
    async with use_tenant(tid):
        f = await wrap(store).policy_documents.find_one({"doc_id": doc_id, "staff_only": {"$ne": True}, "withdrawn_at": None}, {"_id": 0})
    return f if f and f["client_id"] in client_ids else None
