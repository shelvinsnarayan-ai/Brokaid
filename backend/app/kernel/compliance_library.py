"""R2.0 — compliance document library: draft → approved by a Principal (ledgered) → live. Only live docs count for go-live.

PDS and TMD are insurer-issued: the library lists them as "Obtain from insurer" and never generates them.
"""
from __future__ import annotations

import hashlib
import io
import uuid
from datetime import datetime, timezone

from app.db.tenant_safe import wrap
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


def _sections(kind: str) -> list[tuple[str, str]]:
    if kind == "FSG":
        return T.fsg()
    if kind == "PRIVACY_POLICY":
        return T.privacy_policy()
    return [(KINDS[kind][0], {"COLLECTION_NOTICE": T.collection_notice, "GAW": T.gaw, "AFCA": T.afca}[kind]())]


def _plain(sections) -> str:
    return "\n\n".join(f"{h}\n{b}" for h, b in sections)


def render_docx(title: str, sections, watermark: str) -> bytes:
    import docx
    from docx.shared import Pt, RGBColor
    d = docx.Document()
    for part in (d.sections[0].header.paragraphs[0],):
        run = part.add_run(watermark)
        run.bold, run.font.size, run.font.color.rgb = True, Pt(10), RGBColor(0xB0, 0x1E, 0x1E)
    p = d.add_paragraph()
    r = p.add_run(watermark)
    r.bold, r.font.size, r.font.color.rgb = True, Pt(14), RGBColor(0xB0, 0x1E, 0x1E)
    d.add_heading(title, level=0)
    for h, body in sections:
        d.add_heading(h, level=1)
        for para in body.split("\n"):
            if para.strip():
                d.add_paragraph(para)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def render_pdf(title: str, sections, watermark: str) -> bytes:
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

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title=title, invariant=1)
    flow = [Paragraph(escape(watermark), mark), Spacer(1, 8), Paragraph(escape(title), st["Title"])]
    for h, body in sections:
        flow.append(Paragraph(escape(h), st["Heading2"]))
        for para in body.split("\n"):
            if para.strip():
                flow.append(Paragraph(escape(para), st["Normal"]))
        flow.append(Spacer(1, 6))
    doc.build(flow, onFirstPage=header, onLaterPages=header)
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


async def generate_drafts(store, tid: str, *, by: str, role: str) -> list[dict]:
    """Create one DRAFT per kind (idempotent: an existing draft/approved/live doc of that kind is left alone)."""
    brand = await _brand(store, tid)
    mark = T.WATERMARK.format(brand=brand)
    out = []
    safe = wrap(store)
    for kind, (title, _field, _file) in KINDS.items():
        async with use_tenant(tid):
            if await safe.compliance_library.find_one({"kind": kind, "status": {"$in": ["draft", "approved", "live"]}}):
                continue
        sections = _sections(kind)
        doc_id = str(uuid.uuid4())
        docx_b, pdf_b = render_docx(title, sections, mark), render_pdf(title, sections, mark)
        row = {"doc_id": doc_id, "kind": kind, "title": title, "status": "draft", "version": 1, "text": _plain(sections),
               "watermark": mark, "docx_path": await _store_file(tid, doc_id, "docx", docx_b, DOCX),
               "pdf_path": await _store_file(tid, doc_id, "pdf", pdf_b, "application/pdf"),
               "docx_sha256": hashlib.sha256(docx_b).hexdigest(), "pdf_sha256": hashlib.sha256(pdf_b).hexdigest(),
               "final_path": None, "final_sha256": None, "generated": True, "created_by": by, "created_at": _now(), "approval": None}
        async with use_tenant(tid):
            await safe.compliance_library.insert_one(dict(row))
        await _event(store, tid, "ComplianceDraftCreated", {"doc_id": doc_id, "kind": kind, "version": 1,
                     "pdf_sha256": row["pdf_sha256"], "docx_sha256": row["docx_sha256"], "status": "draft"},
                     f"compliance_draft:{doc_id}", by, role)
        out.append(row)
    return out


async def listing(store, tid) -> list[dict]:
    async with use_tenant(tid):
        rows = [r async for r in wrap(store).compliance_library.find({"status": {"$ne": "superseded"}}, {"_id": 0}).sort("created_at", 1)]
    for r in rows:
        r["placeholders"] = r["text"].count(PLACEHOLDER) if not KINDS[r["kind"]][2] or not r.get("final_path") else 0
    return rows + [{"kind": k, "title": t, "status": "obtain_from_insurer", "insurer_issued": True} for k, t in INSURER_ISSUED.items()]


async def get(store, tid, doc_id) -> dict:
    async with use_tenant(tid):
        d = await wrap(store).compliance_library.find_one({"doc_id": doc_id}, {"_id": 0})
    if not d:
        raise Bad(404, "not_found")
    return d


async def revise_text(store, tid, doc_id, text: str, *, by, role) -> dict:
    d = await get(store, tid, doc_id)
    if KINDS[d["kind"]][2]:
        raise Bad(409, "Upload the final, completed FSG as a PDF instead.")
    if d["status"] == "live":
        raise Bad(409, "This version is live. Generate a new draft to change it.")
    text = text.strip()
    async with use_tenant(tid):
        await wrap(store).compliance_library.update_one({"doc_id": doc_id}, {"$set": {"text": text, "status": "draft", "approval": None},
                                                                             "$inc": {"version": 1}})
    await _event(store, tid, "ComplianceDraftRevised", {"doc_id": doc_id, "kind": d["kind"], "version": d["version"] + 1,
                 "sha256": hashlib.sha256(text.encode()).hexdigest()}, f"compliance_rev:{doc_id}:{d['version'] + 1}", by, role)
    return await get(store, tid, doc_id)


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
    return d["final_sha256"] if KINDS[d["kind"]][2] else hashlib.sha256(d["text"].encode()).hexdigest()


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
    if KINDS[d["kind"]][2] and not d.get("final_path"):
        raise Bad(409, "The generated FSG is a template marked DRAFT. Complete it, then upload the final PDF before approving.")
    if not KINDS[d["kind"]][2] and PLACEHOLDER in d["text"]:
        raise Bad(409, f"Fill in every {PLACEHOLDER}…] field before approving.")
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
    if is_file:
        from app.kernel import compliance_docs as cd
        data, _ = await object_store.get_object(d["final_path"])
        await cd.upload(store, tid, kind="FSG", data=data, filename=f"FSG-v{d['version']}.pdf", content_type="application/pdf",
                        version=str(d["version"]), effective_date=_now().date().isoformat(), insurer=None, product=None, by=by)
    else:
        async with use_tenant(tid):
            await safe.tenant_settings.update_one({}, {"$set": {field: d["text"]}}, upsert=True)
    async with use_tenant(tid):
        await safe.compliance_library.update_many({"kind": kind, "status": "live"}, {"$set": {"status": "superseded"}})
        await safe.compliance_library.update_one({"doc_id": doc_id}, {"$set": {"status": "live", "live_at": _now(), "live_by": by}})
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
    if d["version"] > 1 and not KINDS[d["kind"]][2]:  # revised text: re-render so downloads match what will be approved
        sections = [(d["title"], d["text"])]
        data = render_docx(d["title"], sections, d["watermark"]) if fmt == "docx" else render_pdf(d["title"], sections, d["watermark"])
    else:
        data, _ = await object_store.get_object(d["docx_path"] if fmt == "docx" else d["pdf_path"])
    ct = DOCX if fmt == "docx" else "application/pdf"
    return data, ct, f"{d['kind']}-DRAFT-v{d['version']}.{'docx' if fmt == 'docx' else 'pdf'}"


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
