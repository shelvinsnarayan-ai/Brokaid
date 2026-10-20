"""R2.0 — compliance document library: draft → approved by a Principal (ledgered) → live. Only live docs count for go-live.

PDS and TMD are insurer-issued: the library lists them as "Obtain from insurer" and never generates them.
"""
from __future__ import annotations

import difflib
import hashlib
import io
import re
import uuid
from datetime import datetime, timezone

from app.db.tenant_safe import wrap
from app.kernel import business_details as bd
from app.kernel import compliance_templates as T
from app.kernel import filetype, object_store
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch
from app.kernel.trail_mongo import DuplicateIdempotencyKey

CONFIRM = "I confirm this has been reviewed by our licensee/compliance adviser"
# kind → (title, settings field it feeds when live, file-based?)
KINDS = {
    "FSG": ("Financial Services Guide", None, True),
    "PRIVACY_POLICY": ("Privacy policy", "privacy_policy_text", False),
    "COLLECTION_NOTICE": ("Collection notice", "collection_notice_text", False),
    "GAW": ("General advice warning", "gaw_text", False),
    "AFCA": ("Complaints and AFCA text", "afca_text", False),
}
INSURER_ISSUED = {"PDS": "Product Disclosure Statement", "TMD": "Target Market Determination"}
PLACEHOLDER = "[PLACEHOLDER"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class Bad(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _now():
    return datetime.now(timezone.utc)


def _sections(kind: str, p: dict | None = None) -> list[tuple[str, str]]:
    p = p or T.P
    if kind == "FSG":
        return T.fsg(p)
    if kind == "PRIVACY_POLICY":
        return T.privacy_policy(p)
    return [(KINDS[kind][0], {"COLLECTION_NOTICE": T.collection_notice, "GAW": T.gaw, "AFCA": T.afca}[kind](p))]


def _plain(sections) -> str:
    return "\n\n".join(f"{h}\n{b}" for h, b in sections)


_BLANK = re.compile(r"\[PLACEHOLDER:[^\]]*\]")


def compose(kind: str, details: dict, overrides: dict, version: int) -> tuple[list[tuple[str, str]], dict]:
    """Template + business details + section edits → (sections, blanks progress). Edits are filled too."""
    vals = {**bd.fill(details), "date": bd.today(), "version": str(version)}
    fill_p = {k: vals.get(k, v) for k, v in T.P.items()}
    blank_secs, secs = _sections(kind), _sections(kind, fill_p)
    raw = []
    for i, ((h, body), (_h, blank)) in enumerate(zip(secs, blank_secs)):
        if str(i) in overrides:
            blank = overrides[str(i)]
            body = blank
            for k, ph in T.P.items():
                body = body.replace(ph, fill_p[k])
        secs[i] = (h, body)
        raw.append(blank)
    used = [k for k, ph in T.P.items() if k not in bd.AUTO and any(ph in b for b in raw)]  # date/version are automatic
    left = sorted(set(_BLANK.findall(_plain(secs))))
    known_left = [k for k in used if T.P[k] in left]
    unknown = [x for x in left if x not in T.P.values()]
    total = len(used) + len(unknown)
    return secs, {"total": total, "filled": total - len(known_left) - len(unknown),
                  "remaining": [bd.label_for(k) for k in known_left] + unknown}


def render_docx(title: str, sections, watermark: str | None) -> bytes:
    import docx
    from docx.enum.text import WD_COLOR_INDEX
    from docx.shared import Pt, RGBColor
    d = docx.Document()
    if watermark:
        run = d.sections[0].header.paragraphs[0].add_run(watermark)
        run.bold, run.font.size, run.font.color.rgb = True, Pt(10), RGBColor(0xB0, 0x1E, 0x1E)
        r = d.add_paragraph().add_run(watermark)
        r.bold, r.font.size, r.font.color.rgb = True, Pt(14), RGBColor(0xB0, 0x1E, 0x1E)
    d.add_heading(title, level=0)
    for h, body in sections:
        d.add_heading(h, level=1)
        for para in body.split("\n"):
            if para.strip():
                p, pos = d.add_paragraph(), 0
                for m in _BLANK.finditer(para):  # remaining blanks are highlighted
                    p.add_run(para[pos:m.start()])
                    p.add_run(m.group(0)).font.highlight_color = WD_COLOR_INDEX.YELLOW
                    pos = m.end()
                p.add_run(para[pos:])
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def render_pdf(title: str, sections, watermark: str | None) -> bytes:
    from xml.sax.saxutils import escape
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    st = getSampleStyleSheet()
    mark = ParagraphStyle("mark", parent=st["Normal"], textColor=colors.HexColor("#B01E1E"), fontName="Helvetica-Bold", fontSize=12)

    def header(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 9)
        canvas.setFillColor(colors.HexColor("#B01E1E"))
        canvas.drawString(36, A4[1] - 24, watermark)
        canvas.setFont("Helvetica-Bold", 60)
        canvas.setFillColor(colors.Color(0.69, 0.12, 0.12, alpha=0.08))
        canvas.translate(A4[0] / 2, A4[1] / 2)
        canvas.rotate(35)
        canvas.drawCentredString(0, 0, "DRAFT")
        canvas.restoreState()

    hl = lambda s: _BLANK.sub(lambda m: f'<font backColor="#FFF3A3">{m.group(0)}</font>', escape(s))  # noqa: E731
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title=title, invariant=1)
    flow = ([Paragraph(escape(watermark), mark), Spacer(1, 8)] if watermark else []) + [Paragraph(escape(title), st["Title"])]
    for h, body in sections:
        flow.append(Paragraph(escape(h), st["Heading2"]))
        for para in body.split("\n"):
            if para.strip():
                flow.append(Paragraph(hl(para), st["Normal"]))
        flow.append(Spacer(1, 6))
    if watermark:
        doc.build(flow, onFirstPage=header, onLaterPages=header)
    else:
        doc.build(flow)
    return buf.getvalue()


async def _event(store, tid, typ, payload, idem, by, role):
    async with use_tenant(tid):
        try:
            await append_via_dispatch(store, tid, typ, actor={"kind": "human" if by != "system" else "system", "id": by, "name": role},
                                      payload=payload, idempotency_key=idem)
        except DuplicateIdempotencyKey:
            pass


async def _brand(store, tid) -> str:
    t = await store.tenants.find_one({"tenant_id": tid}, {"display_name": 1, "legal_name": 1, "slug": 1}) or {}  # tenant-safe: system-only
    return t.get("display_name") or t.get("legal_name") or t.get("slug") or "the brokerage"


async def _store_file(tid, doc_id, ext, data, ct) -> str:
    path = f"{object_store.APP_NAME}/compliance-library/{tid}/{doc_id}.{ext}"
    await object_store.put_object(path, data, ct)
    return path


async def _new_version(store, tid, d: dict, *, reason: str, by: str, role: str) -> dict:
    """Render the current template + details + edits as a new DRAFT version (DOCX + PDF stored, hashed, ledgered)."""
    details = (await bd.current(store, tid))["fields"]
    v = d.get("version", 0) + 1
    secs, blanks = compose(d["kind"], details, d.get("overrides") or {}, v)
    docx_b, pdf_b = render_docx(d["title"], secs, d["watermark"]), render_pdf(d["title"], secs, d["watermark"])
    text = _plain(secs)
    ver = {"doc_id": d["doc_id"], "version": v, "sections": [list(x) for x in secs], "text": text, "reason": reason,
           "sha256": hashlib.sha256(text.encode()).hexdigest(), "blanks": blanks, "by": by, "at": _now(),
           "docx_path": await _store_file(tid, f"{d['doc_id']}-v{v}", "docx", docx_b, DOCX),
           "pdf_path": await _store_file(tid, f"{d['doc_id']}-v{v}", "pdf", pdf_b, "application/pdf"),
           "docx_sha256": hashlib.sha256(docx_b).hexdigest(), "pdf_sha256": hashlib.sha256(pdf_b).hexdigest()}
    safe = wrap(store)
    upd = {k: ver[k] for k in ("version", "sections", "text", "blanks", "docx_path", "pdf_path", "docx_sha256", "pdf_sha256")}
    async with use_tenant(tid):
        await safe.compliance_library_versions.insert_one(dict(ver))
        if d.get("version"):
            await safe.compliance_library.update_one({"doc_id": d["doc_id"]}, {"$set": {**upd, "status": "draft", "approval": None}})
    await _event(store, tid, "ComplianceDraftCreated" if v == 1 else "ComplianceDraftRevised",
                 {"doc_id": d["doc_id"], "kind": d["kind"], "version": v, "reason": reason, "sha256": ver["sha256"],
                  "pdf_sha256": ver["pdf_sha256"], "docx_sha256": ver["docx_sha256"], "blanks_remaining": len(blanks["remaining"]),
                  "status": "draft"}, f"compliance_rev:{d['doc_id']}:{v}", by, role)
    return {**d, **upd, "status": "draft", "approval": None}


async def generate_drafts(store, tid: str, *, by: str, role: str) -> list[dict]:
    """Create one DRAFT per kind (idempotent: an existing draft/approved/live doc of that kind is left alone)."""
    mark = T.WATERMARK.format(brand=await _brand(store, tid))
    out, safe = [], wrap(store)
    for kind, (title, _field, _file) in KINDS.items():
        async with use_tenant(tid):
            if await safe.compliance_library.find_one({"kind": kind, "status": {"$in": ["draft", "approved", "live"]}}):
                continue
        row = await _new_version(store, tid, {"doc_id": str(uuid.uuid4()), "kind": kind, "title": title, "watermark": mark,
                                              "overrides": {}, "version": 0}, reason="generated", by=by, role=role)
        row.update({"final_path": None, "final_sha256": None, "generated": True, "created_by": by, "created_at": _now()})
        async with use_tenant(tid):
            await safe.compliance_library.insert_one(dict(row))
        row.pop("_id", None)
        out.append(row)
    return out


async def regenerate_all(store, tid: str, *, by: str, role: str) -> list[str]:
    """After business details change: every generated draft/approved (not live) document gets a new draft version."""
    async with use_tenant(tid):
        rows = [r async for r in wrap(store).compliance_library.find({"status": {"$in": ["draft", "approved"]}}, {"_id": 0})]
    return [(await _new_version(store, tid, r, reason="business_details", by=by, role=role))["doc_id"] for r in rows]


async def edit_section(store, tid, doc_id: str, index: int, body: str, *, by, role) -> dict:
    d = await get(store, tid, doc_id)
    if d["status"] == "live":
        raise Bad(409, "This version is live. Generate a new draft to change it.")
    if not d.get("sections"):  # an R2.0 draft: render it the R2.3 way first
        d = await _new_version(store, tid, d, reason="upgrade", by=by, role=role)
    if not 0 <= index < len(d["sections"]):
        raise Bad(404, "not_found")
    ov = {**(d.get("overrides") or {}), str(index): body.strip()}
    async with use_tenant(tid):
        await wrap(store).compliance_library.update_one({"doc_id": doc_id}, {"$set": {"overrides": ov}})
    return await _new_version(store, tid, {**d, "overrides": ov}, reason=f"edit:section {index + 1}", by=by, role=role)


async def versions(store, tid, doc_id) -> list[dict]:
    await get(store, tid, doc_id)
    async with use_tenant(tid):
        return [v async for v in wrap(store).compliance_library_versions.find(
            {"doc_id": doc_id}, {"_id": 0, "version": 1, "reason": 1, "sha256": 1, "pdf_sha256": 1, "blanks": 1, "by": 1, "at": 1}
        ).sort("version", 1)]


async def diff(store, tid, doc_id, a: int, b: int) -> list[dict]:
    await get(store, tid, doc_id)
    async with use_tenant(tid):
        va = await wrap(store).compliance_library_versions.find_one({"doc_id": doc_id, "version": a}, {"_id": 0, "text": 1})
        vb = await wrap(store).compliance_library_versions.find_one({"doc_id": doc_id, "version": b}, {"_id": 0, "text": 1})
    if not va or not vb:
        raise Bad(404, "not_found")
    return [{"op": ln[0], "line": ln[2:]} for ln in difflib.ndiff(va["text"].splitlines(), vb["text"].splitlines()) if ln[0] in "+- "]


async def listing(store, tid) -> list[dict]:
    async with use_tenant(tid):
        rows = [r async for r in wrap(store).compliance_library.find({"status": {"$nin": ["superseded", "withdrawn", "archived"]}}, {"_id": 0}).sort("created_at", 1)]
    for r in rows:
        b = r.get("blanks") or {"total": 0, "filled": 0, "remaining": []}
        r["blanks"] = {"total": 0, "filled": 0, "remaining": []} if r.get("final_path") else b
        r["placeholders"] = len(r["blanks"]["remaining"])
    return rows + [{"kind": k, "title": t, "status": "obtain_from_insurer", "insurer_issued": True} for k, t in INSURER_ISSUED.items()]


async def get(store, tid, doc_id) -> dict:
    async with use_tenant(tid):
        d = await wrap(store).compliance_library.find_one({"doc_id": doc_id}, {"_id": 0})
    if not d:
        raise Bad(404, "not_found")
    return d


async def upload_final(store, tid, doc_id, data: bytes, *, by, role) -> dict:
    d = await get(store, tid, doc_id)
    if not KINDS[d["kind"]][2]:
        raise Bad(409, "Edit the text instead.")
    if filetype.sniff(data) != "application/pdf":
        raise Bad(422, "Upload the completed FSG as a PDF.")
    sha = hashlib.sha256(data).hexdigest()
    path = await _store_file(tid, f"{doc_id}-final-{sha[:12]}", "pdf", data, "application/pdf")
    async with use_tenant(tid):
        await wrap(store).compliance_library.update_one({"doc_id": doc_id}, {"$set": {"final_path": path, "final_sha256": sha,
                                                        "status": "draft", "approval": None}, "$inc": {"version": 1}})
    await _event(store, tid, "ComplianceDraftRevised", {"doc_id": doc_id, "kind": d["kind"], "version": d["version"] + 1, "sha256": sha,
                 "final_upload": True}, f"compliance_rev:{doc_id}:{d['version'] + 1}", by, role)
    return await get(store, tid, doc_id)


def _approved_sha(d) -> str:
    return d["final_sha256"] if d.get("final_path") else hashlib.sha256(d["text"].encode()).hexdigest()


async def approve(store, tid, doc_id, *, reviewer_name: str, confirmed: bool, by, role) -> dict:
    if role != "principal":
        raise Bad(403, "Only a Principal can approve compliance documents.")
    if not confirmed:
        raise Bad(422, f"Tick “{CONFIRM}”.")
    if len((reviewer_name or "").strip()) < 2:
        raise Bad(422, "Enter the reviewer's name.")
    d = await get(store, tid, doc_id)
    if d["status"] != "draft":
        raise Bad(409, f"This document is {d['status']}, not a draft.")
    if not d.get("final_path") and PLACEHOLDER in d["text"]:  # R2.3: a generated draft with zero blanks may be approved
        n = len((d.get("blanks") or {}).get("remaining") or []) or d["text"].count(PLACEHOLDER)
        raise Bad(409, f"{n} blank{'s' if n != 1 else ''} still to fill. Fill every blank (or upload a finished PDF) before approving.")
    approval = {"reviewer_name": reviewer_name.strip()[:120], "confirmation": CONFIRM, "by": by, "at": _now(), "sha256": _approved_sha(d)}
    async with use_tenant(tid):
        await wrap(store).compliance_library.update_one({"doc_id": doc_id}, {"$set": {"status": "approved", "approval": approval}})
    await _event(store, tid, "ComplianceDocumentApproved", {"doc_id": doc_id, "kind": d["kind"], "version": d["version"],
                 "sha256": approval["sha256"], "reviewer_name": approval["reviewer_name"], "confirmation": CONFIRM},
                 f"compliance_approve:{doc_id}:{d['version']}", by, role)
    return await get(store, tid, doc_id)


async def make_live(store, tid, doc_id, *, by, role) -> dict:
    if role not in ("principal", "operator"):
        raise Bad(403, "Only a Principal or the Operator can make a document live.")
    d = await get(store, tid, doc_id)
    if d["status"] != "approved":
        raise Bad(409, "Only a Principal-approved document can go live.")
    kind, (_title, field, is_file) = d["kind"], KINDS[d["kind"]]
    safe = wrap(store)
    secs = [tuple(x) for x in d.get("sections") or [(d["title"], d["text"])]]
    clean_pdf = render_pdf(d["title"], secs, None)  # live = no DRAFT watermark
    clean = {"live_pdf_path": await _store_file(tid, f"{doc_id}-v{d['version']}-live", "pdf", clean_pdf, "application/pdf"),
             "live_docx_path": await _store_file(tid, f"{doc_id}-v{d['version']}-live", "docx", render_docx(d["title"], secs, None), DOCX),
             "live_pdf_sha256": hashlib.sha256(clean_pdf).hexdigest()}
    if is_file:
        from app.kernel import compliance_docs as cd
        data = (await object_store.get_object(d["final_path"]))[0] if d.get("final_path") else clean_pdf
        await cd.upload(store, tid, kind="FSG", data=data, filename=f"FSG-v{d['version']}.pdf", content_type="application/pdf",
                        version=str(d["version"]), effective_date=_now().date().isoformat(), insurer=None, product=None, by=by)
    else:
        async with use_tenant(tid):
            await safe.tenant_settings.update_one({}, {"$set": {field: d["text"]}}, upsert=True)
    async with use_tenant(tid):
        await safe.compliance_library.update_many({"kind": kind, "status": "live"}, {"$set": {"status": "superseded"}})
        await safe.compliance_library.update_one({"doc_id": doc_id}, {"$set": {"status": "live", "live_at": _now(), "live_by": by, **clean}})
    await _event(store, tid, "ComplianceDocumentLive", {"doc_id": doc_id, "kind": kind, "version": d["version"],
                 "sha256": d["approval"]["sha256"]}, f"compliance_live:{doc_id}:{d['version']}", by, role)
    return await get(store, tid, doc_id)


async def live_kinds(store, tid) -> set[str]:
    async with use_tenant(tid):
        return set(await wrap(store).compliance_library.distinct("kind", {"status": "live"}))


async def download(store, tid, doc_id, fmt: str) -> tuple[bytes, str, str]:
    d = await get(store, tid, doc_id)
    if fmt == "final" and d.get("final_path"):
        data, _ = await object_store.get_object(d["final_path"])
        return data, "application/pdf", f"{d['kind']}-v{d['version']}-final.pdf"
    live = d["status"] in ("live", "superseded") and d.get("live_pdf_path")
    path = d.get(f"live_{fmt}_path" if live else f"{fmt}_path") or d[f"{'docx' if fmt == 'docx' else 'pdf'}_path"]
    data, _ = await object_store.get_object(path)
    ct = DOCX if fmt == "docx" else "application/pdf"
    return data, ct, f"{d['kind']}-{'v' if live else 'DRAFT-v'}{d['version']}.{'docx' if fmt == 'docx' else 'pdf'}"


async def drafts_zip(store, tid) -> bytes:
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for r in await listing(store, tid):
            if r.get("insurer_issued"):
                continue
            for fmt in ("docx", "pdf"):
                data, _, name = await download(store, tid, r["doc_id"], fmt)
                z.writestr(name, data)
        z.writestr("README.txt", "DRAFT compliance templates generated by brokAId. Every [PLACEHOLDER] must be completed and the "
                   "documents reviewed by the brokerage's licensee/compliance adviser. They are not legal advice and must not be "
                   "given to clients until a Principal approves them in brokAId and they are made live.\n"
                   "PDS and TMD are issued by insurers: obtain them from each insurer.\n")
    return buf.getvalue()


async def ensure_indexes(store) -> None:
    # tenant-safe: system-only — index DDL
    await store.compliance_library.create_index([("tenant_id", 1), ("doc_id", 1)], unique=True)
