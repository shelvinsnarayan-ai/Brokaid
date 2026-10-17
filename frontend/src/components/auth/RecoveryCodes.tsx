import { useState } from "react";

export function RecoveryCodes({ codes, onContinue }: { codes: string[]; onContinue: () => void }) {
  const [saved, setSaved] = useState(false);
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard?.writeText(codes.join("\n")).catch(() => undefined);
    setCopied(true);
  };
  return (
    <div className="auth-steps" data-testid="recovery-codes">
      <p className="auth-step">Step 3 of 3 · Save your recovery codes</p>
      <p className="auth-copy">If you lose your phone, each code lets you sign in once. Keep them somewhere safe. You won't see them again.</p>
      <ol className="auth-codes" data-testid="recovery-codes-list">
        {codes.map((c) => <li key={c}><code>{c}</code></li>)}
      </ol>
      <button type="button" className="auth-btn auth-btn--ghost" onClick={copy} data-testid="recovery-codes-copy">
        {copied ? "Copied" : "Copy codes"}
      </button>
      <label className="auth-check">
        <input type="checkbox" checked={saved} onChange={(e) => setSaved(e.target.checked)} data-testid="recovery-codes-saved" />
        I've saved my recovery codes
      </label>
      <button type="button" className="auth-btn" disabled={!saved} onClick={onContinue} data-testid="recovery-codes-continue">
        Continue
      </button>
    </div>
  );
}
