"""R1.7 — upload FICTIONAL demo disclosure documents (FSG + PDS/TMD per insurer) to the Harbourline demo tenant only.

Usage: cd backend && python -m app.scripts.r17_demo_disclosures
BurMac's real FSG/PDS/TMD/wording are supplied by the owner and uploaded in /o/compliance.
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from app.kernel import compliance_docs as cd  # noqa: E402
from app.kernel import object_store  # noqa: E402
from app.kernel import renewals as rn  # noqa: E402


def _pdf(title: str) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for i, ln in enumerate([title, "FICTIONAL DEMO DOCUMENT — not a real disclosure document.", "Harbourline demo tenant only."]):
        c.drawString(48, 800 - 20 * i, ln)
    c.save()
    return buf.getvalue()


async def main() -> None:
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    object_store.init_storage()
    t = await db.tenants.find_one({"slug": "harbourline"})  # tenant-safe: system-only
    tid = t["tenant_id"]
    if not await cd.active(db, tid, "FSG"):
        await cd.upload(db, tid, kind="FSG", data=_pdf("Harbourline Insurance Brokers — Financial Services Guide v1.0"), filename="harbourline-fsg.pdf",
                        content_type="application/pdf", version="1.0-demo", effective_date="2026-07-01", insurer=None, product=None, by="seed")
    for ins in await rn.insurers(db, tid):
        for kind in ("PDS", "TMD"):
            if not await cd.active(db, tid, kind, ins):
                await cd.upload(db, tid, kind=kind, data=_pdf(f"{ins} — {kind} (all products)"), filename=f"{kind.lower()}-{ins[:20]}.pdf",
                                content_type="application/pdf", version="1.0-demo", effective_date="2026-07-01", insurer=ins, product=None, by="seed")
    print("Harbourline demo disclosures:", await db.disclosure_docs.count_documents({"tenant_id": tid, "active": True}))  # tenant-safe: system-only


if __name__ == "__main__":
    asyncio.run(main())
