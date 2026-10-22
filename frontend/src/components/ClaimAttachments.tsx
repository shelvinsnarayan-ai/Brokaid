/** R3.5 — photos/videos on a claim message (client + staff). Files are private; `base` is the authenticated files route. */
import { useState } from "react";
import "../styles/claimAttachments.css";

export interface Att { file_id: string; kind: "photo" | "video" | "document"; content_type: string; filename: string; size: number; duration_s?: number | null; has_preview: boolean; label?: string | null }

const fmtDur = (s?: number | null) => (s ? `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}` : "");
const fmtSize = (n: number) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);
const LABEL: Record<string, string> = { repair_quote: "Repair quote", assessor_report: "Assessor report", insurer_letter: "Insurer letter", photo: "Photo", other: "Document" };

export function ClaimAttachments({ atts, base, testid }: { atts: Att[]; base: string; testid: string }) {
  const [open, setOpen] = useState<Att | null>(null);
  if (!atts?.length) return null;
  const src = (a: Att, preview = false) => `${base}/${a.file_id}${preview ? "?preview=true" : ""}`;
  return (
    <div className="cat-grid" data-testid={testid}>
      {atts.map((a, i) => a.kind === "document" ? (
        <div key={a.file_id} className="cat-doc" data-testid={`${testid}-doc-${i}`}>
          <span className="cat-doc__icon" aria-hidden="true">PDF</span>
          <span className="cat-doc__body">
            <span className="cat-doc__name" data-testid={`${testid}-doc-name-${i}`}>{a.filename}</span>
            <span className="cat-doc__meta">{LABEL[a.label || "other"] || "Document"} · {fmtSize(a.size)}</span>
            <span className="cat-doc__acts">
              <a href={src(a)} target="_blank" rel="noreferrer" data-testid={`${testid}-doc-open-${i}`}>Open</a>
              <a href={`${src(a)}?download=true`} data-testid={`${testid}-doc-download-${i}`}>Download</a>
            </span>
          </span>
        </div>
      ) : a.kind === "video" ? (
        <div key={a.file_id} className="cat-video" data-testid={`${testid}-video-${i}`}>
          <video controls playsInline preload="metadata" poster={a.has_preview ? src(a, true) : undefined} src={src(a)} />
          <a href={`${src(a)}?download=true`} className="cat-dl" data-testid={`${testid}-video-download-${i}`}>Download video{a.duration_s ? ` · ${fmtDur(a.duration_s)}` : ""}</a>
        </div>
      ) : (
        <button key={a.file_id} type="button" className="cat-thumb" onClick={() => setOpen(a)} data-testid={`${testid}-photo-${i}`}
          aria-label={`Open photo ${i + 1}`}>
          <img src={src(a, a.has_preview)} alt={`Claim photo ${i + 1}`} loading="lazy" />
        </button>
      ))}
      {open ? (
        <div className="cat-box" role="dialog" aria-modal="true" aria-label="Photo" onClick={() => setOpen(null)} data-testid={`${testid}-lightbox`}>
          <img src={src(open, open.has_preview)} alt="Claim photo, full size" onClick={(e) => e.stopPropagation()} />
          <div className="cat-box__bar" onClick={(e) => e.stopPropagation()}>
            <a href={`${src(open)}?download=true`} data-testid={`${testid}-lightbox-download`}>Download original</a>
            <button type="button" onClick={() => setOpen(null)} data-testid={`${testid}-lightbox-close`}>Close</button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
