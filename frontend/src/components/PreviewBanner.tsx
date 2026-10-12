/**
 * PreviewBanner — visible when the active jurisdiction preview differs
 * from the client's actual (server-authoritative) jurisdiction. Styled
 * like the MockedBadge to reinforce the "not real, demo/preview" cue.
 */
import { isPreviewMode, useMeStore, usePack } from "../state/meStore";

export function PreviewBanner() {
  const me = useMeStore((s) => s.me);
  const pack = usePack();
  if (!isPreviewMode(me) || !pack || !me?.linked_client) return null;
  return (
    <div
      className="brokaid-preview-banner"
      role="status"
      data-testid="preview-banner"
    >
      <span className="brokaid-preview-banner__pill">
        PREVIEW
      </span>
      Rendering as <strong>{pack.display_name}</strong>. This client is
      actually registered in <strong>{me.linked_client.jurisdiction_code}</strong>.
    </div>
  );
}
