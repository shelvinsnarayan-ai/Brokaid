/** R3.5/R3.6 — pre-send tiles: thumbnail, progress, remove, single retry; optional file label (broker replies). */
import type { Item } from "./attachUpload";
import "../../styles/attachTray.css";

const LABELS: [string, string][] = [["repair_quote", "Repair quote"], ["assessor_report", "Assessor report"], ["insurer_letter", "Insurer letter"],
  ["photo", "Photo"], ["other", "Other"]];

export function AttachTray({ items, base, onRemove, onRetry, onLabel, testid }: {
  items: Item[]; base: string; onRemove: (k: string) => void; onRetry: (k: string) => void; onLabel?: (k: string, l: string) => void; testid: string;
}) {
  if (!items.length) return null;
  return (
    <ul className="cat-tray" data-testid={`${testid}-tray`}>
      {items.map((it, i) => {
        const heic = /hei[cf]$/i.test(it.file.name) || /hei[cf]/.test(it.file.type);
        const thumb = it.status === "done" && it.has_preview ? `${base}/${it.file_id}/preview` : it.url;
        return (
          <li key={it.key} className={`cat-tile cat-tile--${it.status}${onLabel ? " cat-tile--lbl" : ""}`} data-testid={`${testid}-tile-${i}`}>
            {it.kind === "document" ? <span className="cat-tile__ph cat-tile__doc"><b>PDF</b><span>{it.file.name}</span></span>
              : it.kind === "video" && !(it.status === "done" && it.has_preview)
              ? <video src={it.url} muted playsInline preload="metadata" aria-label={it.file.name} />
              : heic && !(it.status === "done" && it.has_preview) ? <span className="cat-tile__ph">HEIC photo</span>
                : <img src={thumb} alt={it.file.name} />}
            {it.kind === "video" ? <span className="cat-tile__tag">Video</span> : null}
            {it.status === "uploading" ? (
              <span className="cat-prog" role="progressbar" aria-valuenow={it.progress} aria-valuemin={0} aria-valuemax={100}
                data-testid={`${testid}-progress-${i}`}><span style={{ width: `${it.progress}%` }} /></span>
            ) : null}
            {it.status === "error" ? (
              <span className="cat-tile__err" role="alert" data-testid={`${testid}-error-${i}`}>{it.error}
                {it.retryable && it.tries < 2 ? <button type="button" onClick={() => onRetry(it.key)} data-testid={`${testid}-retry-${i}`}>Retry</button> : null}
              </span>
            ) : null}
            <button type="button" className="cat-tile__x" onClick={() => onRemove(it.key)} aria-label={`Remove ${it.file.name}`}
              data-testid={`${testid}-remove-${i}`}>×</button>
            {onLabel ? (
              <select className="cat-tile__lbl" value={it.label} onChange={(e) => onLabel(it.key, e.target.value)} aria-label={`Label for ${it.file.name}`}
                data-testid={`${testid}-label-${i}`}>
                {LABELS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
              </select>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
