/**
 * BuildIndicator — footer-line pill on the operator shell (Ledger only).
 *
 * Reads two Vite-injected globals baked at build time:
 *   import.meta.env.BROKAID_BUILD_SHA    — short (7-char) git SHA of the built commit
 *   import.meta.env.BROKAID_BUILD_SIGNED — "G" (good), "N" (not signed), "?" (unknown)
 *
 * Rendered as e.g.  `build 51150d3 · G`.
 *
 * Low visual weight (Slate-strong on Bone, monospace). aria-labelled for
 * screen readers, `tabIndex={-1}` so it never lands in keyboard focus.
 * NEVER rendered on the client shell — this is operator credibility only.
 */
export function BuildIndicator() {
  const sha = import.meta.env.BROKAID_BUILD_SHA || "unknown";
  const sig = import.meta.env.BROKAID_BUILD_SIGNED || "?";
  return (
    <div
      className="ledger-build-indicator"
      role="contentinfo"
      aria-label={`Build ${sha}, ${sig === "G" ? "signed" : sig === "N" ? "unsigned" : "signature unknown"}`}
      tabIndex={-1}
      data-testid="operator-build-indicator"
    >
      <span className="ledger-build-indicator__label">build</span>
      <span className="ledger-build-indicator__sha" data-testid="operator-build-sha">{sha}</span>
      <span className="ledger-build-indicator__sep" aria-hidden="true">·</span>
      <span
        className={`ledger-build-indicator__sig ledger-build-indicator__sig--${sig.toLowerCase()}`}
        data-testid="operator-build-sig"
      >
        {sig}
      </span>
    </div>
  );
}
