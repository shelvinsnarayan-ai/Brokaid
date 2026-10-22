/** R3.5 — photos/videos on a claim message (client + staff). Files are private; `base` is the authenticated files route. */
import { useState } from "react";
import "../styles/claimAttachments.css";

export interface Att { file_id: string; kind: "photo" | "video"; content_type: string; filename: string; size: number; duration_s?: number | null; has_preview: boolean }

const fmtDur = (s?: number | null) => (s ? `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}` : "");

export function ClaimAttachments({ atts, base, testid }: { atts: Att[]; base: string; testid: string }) {
  const [open, setOpen] = useState<Att | null>(null);
  if (!atts?.length) return null;
  const src = (a: Att, preview = false) => `${base}/${a.file_id}${preview ? "?preview=true" : ""}`;
  return (
    <div className="cat-grid" data-testid={testid}>
      {atts.map((a, i) => a.kind === "video" ? (
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
