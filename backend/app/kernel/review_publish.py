"""R3.11: publish a multi-model review: consolidate, write MD + PDF (with thumbnails), store everything in Object Storage, and ledger ReviewPublished."""
from __future__ import annotations

import io
import json
import uuid
from datetime import datetime
from pathlib import Path

from app.kernel import object_store, reviews as rv
from app.kernel.renewals import _event

PREFIX = "brokaid/reviews"


def _fmt(v) -> str:
    return "–" if v is None else (f"{v:.2f}" if isinstance(v, float) else str(v))


def markdown(review_id: str, at: datetime, man: dict, results: list[dict], con: dict) -> str:
    names = [r["model"] for r in results]
    L = ["# R3.11 Multi-model UX review against A20 (preview build)", "",
         f"Review `{review_id}` · {at:%Y-%m-%d %H:%M} UTC · build {man.get('sha', '?')} · preview (Harbourline demo), not production.",
         "Reviewers (independent, same inputs): " + ", ".join(f"{r['model']} (`{r['model_id']}`)" for r in results), "",
         "## Headline", "", "| Surface | " + " | ".join(names) + " | Consensus | A20 baseline | Rows ≥3.0 |", "|---|" + "---|" * (len(names) + 3)]
    for s in rv.SURFACES:
        m = con["surfaces"][s]
        L.append(f"| {s} | " + " | ".join(_fmt(m["per_model"][n]) for n in names) + f" | **{_fmt(m['consensus'])}** | {m['baseline']} | {m['rows_passing']}/{m['rows_scored']} |")
    L += ["", "## §2 Heuristic matrix (0–4, bar ≥3.0)", "",
          "| Row | Surface | " + " | ".join(names) + " | Mean | Min–max | Baseline | Δ | Pass | Disagree |", "|---|---|" + "---|" * (len(names) + 6)]
    for r in con["rows"]:
        L.append(f"| {r['label']} | {r['surface']} | " + " | ".join(_fmt(r["scores"][n]) for n in names) +
                 f" | {_fmt(r['mean'])} | {_fmt(r['min'])}–{_fmt(r['max'])} | {_fmt(r['baseline'])} | {_fmt(r['delta'])} | "
                 f"{'–' if r['pass'] is None else ('yes' if r['pass'] else 'no')} | {'⚠ ' + str(r['spread']) if r['disagreement'] else ''} |")
    L += ["", "## Ranked fixes (priority = models proposing a fix × gap to 3.0)", ""]
    for i, f in enumerate(con["fixes"][:15], 1):
        L.append(f"{i}. **{f['label']} — {f['surface']}** (priority {f['priority']}: {f['frequency']} model(s) × gap {f['gap']})")
        L += [f"   - {m}: {t}" for m, t in f["fixes"].items()]
    L += ["", "## §3 Task matrix (measured at 390 px; consensus of 4 models)", ""]
    for t in man["tasks"]:
        L.append(f"- **{t['job']} {t['title']}**: measured {t['taps']} tap(s), {t['screens']} screen change(s); goal screen reached: "
                 f"{'yes' if t['reached_goal_screen'] else 'no'}; {t['note']}. Sequence: {', '.join(t['sequence'])}")
    L += ["", "| Job | " + " | ".join(lbl for _, lbl in rv.ARCHETYPES) + " |", "|---|" + "---|" * len(rv.ARCHETYPES)]
    for jid, title in rv.JOBS:
        cells = [next(c for c in con["tasks"] if c["job"] == jid and c["archetype"] == a) for a, _ in rv.ARCHETYPES]
        L.append(f"| {jid} {title} | " + " | ".join(("n/a (A20)" if c["a20_na"] else c["consensus"]) + ("" if c["unanimous"] else "*") for c in cells) + " |")
    L += ["", "\\* = models disagreed.", "", "## Evidence", ""]
    L += [f"- `{s['id']}` — {s['caption']} ({s['url']})" for s in man["screens"]]
    L += ["", "## Appendix: raw scores and comments (verbatim per model)", ""]
    for r in results:
        L += [f"### {r['model']} (`{r['model_id']}`)", "", "```json", json.dumps(r["output"], indent=1, ensure_ascii=False), "```", ""]
    return "\n".join(L)


def pdf(md_text: str, man: dict, shots_dir: Path) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Image as RLImage, Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table, TableStyle
    st = getSampleStyleSheet()
    small = st["BodyText"].clone("small", fontSize=7, leading=9)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=12 * mm)
    flow, table, in_code, code = [], [], False, []
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("**", "")  # noqa: E731

    def flush():
        if table:
            t = Table([[Paragraph(esc(c), small) for c in row] for row in table], repeatRows=1)
            t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, "#999999"), ("BACKGROUND", (0, 0), (-1, 0), "#e8edf3"), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            flow.extend([t, Spacer(1, 4)])
            table.clear()
    for line in md_text.splitlines():
        if line.startswith("```"):
            if in_code:
                flow.append(Preformatted("\n".join(code)[:60000], st["Code"].clone("c", fontSize=5, leading=6)))
                code = []
            in_code = not in_code
            continue
        if in_code:
            code.append(line)
            continue
        if line.startswith("|"):
            if not set(line.replace("|", "").strip()) <= set("-: "):
                table.append([c.strip() for c in line.strip("|").split("|")])
            continue
        flush()
        if line.startswith("## Evidence"):
            flow.append(Paragraph("Evidence (thumbnails)", st["Heading2"]))
            cells = []
            for s in man["screens"]:
                p = shots_dir / s["file"]
                if p.exists():
                    from PIL import Image as PI
                    im = PI.open(p).convert("RGB")
                    im = im.crop((0, 0, im.width, min(im.height, int(im.width * 1.75))))
                    im.thumbnail((360, 640))
                    tb = io.BytesIO()
                    im.save(tb, "JPEG", quality=55)
                    tb.seek(0)
                    tw = 40 * mm
                    cells.append([RLImage(tb, width=tw, height=min(tw * im.height / im.width, 70 * mm)), Paragraph(f"<b>{s['id']}</b><br/>{esc(s['caption'])}", small)])
            rows = [sum(cells[i:i + 3], []) for i in range(0, len(cells), 3)]
            if rows:
                rows[-1] += [""] * (6 - len(rows[-1]))
                flow.append(Table(rows, colWidths=[42 * mm, 20 * mm] * 3))
            continue
        if line.startswith("# "):
            flow.append(Paragraph(esc(line[2:]), st["Title"]))
        elif line.startswith("## "):
            flow.append(Paragraph(esc(line[3:]), st["Heading2"]))
        elif line.startswith("### "):
            flow.append(Paragraph(esc(line[4:]), st["Heading3"]))
        elif line.strip() and not line.startswith("- `"):
            flow.append(Paragraph(esc(line.strip()), small))
    flush()
    doc.build(flow)
    return buf.getvalue()


async def publish(db, d: str, man: dict, results: list[dict], prompt: str, at: datetime) -> dict:
    from app.kernel import devmode
    t = await db.tenants.find_one({"slug": "harbourline"})  # tenant-safe: system-only — Harbourline demo tenant by slug
    if not t or not devmode.is_demo_row(t):
        raise SystemExit("Harbourline demo tenant not found")
    tid, review_id = t["tenant_id"], f"R3.11-{at:%Y%m%d-%H%M}-{uuid.uuid4().hex[:6]}"
    models = {r["model"]: r["output"] for r in results}
    con = rv.consolidate(models)
    md = markdown(review_id, at, man, results, con)
    shots = Path(d, "shots")
    base = f"{PREFIX}/{review_id}"
    files = {"md": f"{base}/R3.11_multi_model_review.md", "pdf": f"{base}/R3.11_multi_model_review.pdf"}
    await object_store.put_object(files["md"], md.encode(), "text/markdown")
    await object_store.put_object(files["pdf"], pdf(md, man, shots), "application/pdf")
    raw = {}
    for r in results:
        key = r["model_id"]
        raw[key] = f"{base}/raw/{key}.json"
        await object_store.put_object(raw[key], json.dumps({k: r[k] for k in ("model", "provider", "model_id", "output", "raw", "attempts")},
                                                           indent=1, ensure_ascii=False, default=str).encode(), "application/json")
    ev = {}
    for s in man["screens"]:
        ev[s["id"]] = f"{base}/evidence/{s['file']}"
        await object_store.put_object(ev[s["id"]], (shots / s["file"]).read_bytes(), "image/png")
    await object_store.put_object(f"{base}/context_pack.txt", prompt.encode(), "text/plain")
    doc = {"tenant_id": tid, "review_id": review_id, "title": "R3.11 multi-model review against A20", "created_at": at, "sha": man.get("sha"),
           "models": [{"name": r["model"], "model_id": r["model_id"], "attempts": r["attempts"]} for r in results],
           "consolidated": con, "files": files, "raw": raw, "evidence": ev, "tasks_measured": man["tasks"]}
    await db.reviews.insert_one(doc)  # tenant-safe: system-only — written with tenant_id
    await _event(db, tid, "ReviewPublished", {"review_id": review_id, "models": [r["model_id"] for r in results],
                 "client_consensus": con["surfaces"]["client"]["consensus"], "broker_consensus": con["surfaces"]["broker"]["consensus"],
                 "pdf": files["pdf"]}, f"review_published:{review_id}", "system", "operator")
    Path(d, "R3.11_multi_model_review.md").write_text(md)
    return {"review_id": review_id, "surfaces": con["surfaces"], "top_fixes": [(f["label"], f["surface"], f["priority"]) for f in con["fixes"][:5]]}
