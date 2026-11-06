/** R2.0 — staff: record a wet-signed scan for insurers that need one (OfflineSignatureRecorded). */
import { useState } from "react";
import "../../styles/acceptance.css";
import { StaffHelp } from "../../components/ledger/StaffHelp";

const API = import.meta.env.VITE_API_BASE || "";

export function OfflineSign({ kind, refId, onDone }: { kind: "endorsement" | "renewal"; refId: string; onDone?: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  async function send() {
    if (!file) return;
    setBusy(true); setMsg(null);
    const fd = new FormData();
    fd.append("file", file);
    fd.append("reason", reason);
    const r = await fetch(`${API}/api/o/acceptance/${kind}/${refId}/offline`, { method: "POST", credentials: "include", body: fd });
    const b = await r.json().catch(() => ({}));
    setBusy(false);
    setMsg(r.ok ? "Signed offline — recorded." : typeof b.detail === "string" ? b.detail : "That didn't work.");
    if (r.ok) onDone?.();
  }
  return (
    <details className="acc-offline" data-testid={`offline-sign-${kind}`}>
      <summary>Signed offline (insurer needs a wet signature) <StaffHelp k="signed_offline" testid="help-signed-offline" /></summary>
      <input type="file" accept="application/pdf,image/jpeg,image/png,image/heic" onChange={(e) => setFile(e.target.files?.[0] || null)}
        data-testid="offline-sign-file" />
      <input placeholder="Reason, e.g. insurer requires a wet signature" value={reason} onChange={(e) => setReason(e.target.value)}
        data-testid="offline-sign-reason" />
      <button className="k-btn" disabled={!file || !reason.trim() || busy} onClick={send} data-testid="offline-sign-submit">
        {busy ? "Uploading…" : "Record signed scan"}</button>
      {msg ? <p className="acc-note" data-testid="offline-sign-msg">{msg}</p> : null}
    </details>
  );
}
