/** R3.5 — pre-send tiles: thumbnail, progress, remove, single retry. */
import type { Item } from "./attachUpload";

const API = (import.meta as any).env.VITE_API_BASE || "";

export function AttachTray({ items, claimId, onRemove, onRetry }: {
  items: Item[]; claimId: string; onRemove: (k: string) => void; onRetry: (k: string) => void;
}) {
  if (!items.length) return null;
  return (
    <ul className="cat-tray" data-testid="mer-attach-tray">
      {items.map((it, i) => {
        const heic = /hei[cf]$/i.test(it.file.name) || /hei[cf]/.test(it.file.type);
        const thumb = it.status === "done" && it.has_preview ? `${API}/api/c/claims/${claimId}/thread/attachments/${it.file_id}/preview` : it.url;
        return (
          <li key={it.key} className={`cat-tile cat-tile--${it.status}`} data-testid={`mer-attach-tile-${i}`}>
            {it.kind === "video" && !(it.status === "done" && it.has_preview)
              ? <video src={it.url} muted playsInline preload="metadata" aria-label={it.file.name} />
              : heic && !(it.status === "done" && it.has_preview) ? <span className="cat-tile__ph">HEIC photo</span>
                : <img src={thumb} alt={it.file.name} />}
            {it.kind === "video" ? <span className="cat-tile__tag">Video</span> : null}
            {it.status === "uploading" ? (
              <span className="cat-prog" role="progressbar" aria-valuenow={it.progress} aria-valuemin={0} aria-valuemax={100}
                data-testid={`mer-attach-progress-${i}`}><span style={{ width: `${it.progress}%` }} /></span>
            ) : null}
            {it.status === "error" ? (
              <span className="cat-tile__err" role="alert" data-testid={`mer-attach-error-${i}`}>{it.error}
                {it.retryable && it.tries < 2 ? <button type="button" onClick={() => onRetry(it.key)} data-testid={`mer-attach-retry-${i}`}>Retry</button> : null}
              </span>
            ) : null}
            <button type="button" className="cat-tile__x" onClick={() => onRemove(it.key)} aria-label={`Remove ${it.file.name}`}
              data-testid={`mer-attach-remove-${i}`}>×</button>
          </li>
        );
      })}
    </ul>
  );
}
