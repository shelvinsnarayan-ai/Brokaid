import { useState } from "react";
import { API, day, useLoad } from "./api";

export function Docs({ role }: { role?: string }) {
  const { data, err, load, setErr } = useLoad<any>("o/compliance/documents");
  const [f, setF] = useState({ kind: "PDS", version: "", effective_date: "", insurer: "", product: "" });
  const [file, setFile] = useState<File | null>(null);
  const can = role === "principal" || role === "operator";
  async function upload() {
    if (!file) return;
    setErr(null);
    const fd = new FormData();
    fd.append("file", file);
    Object.entries(f).forEach(([k, v]) => v && fd.append(k, v));
    const r = await fetch(`${API}/api/o/compliance/documents`, { method: "POST", body: fd, credentials: "include" });
    const b = await r.json().catch(() => ({}));
    if (!r.ok) setErr(typeof b.detail === "string" ? b.detail : `Upload failed (${r.status})`);
    else { setFile(null); load(); }
  }
  return (
    <div className="imp-page" data-testid="disclosure-tab">
      {can ? (
        <div className="imp-card" data-testid="disclosure-upload">
          <h2 className="imp-h2">Upload a disclosure document</h2>
          <p className="imp-muted">Insurer-issued PDS, TMD and wording. Your own FSG goes live through the library above (Principal approval). For retail clients, the PDS and TMD go out with every quote or renewal option.</p>
          <div className="imp-row">
            <select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })} data-testid="disclosure-kind">
              {(data?.kinds || ["PDS", "TMD", "WORDING"]).filter((k: string) => k !== "FSG").map((k: string) => <option key={k}>{k}</option>)}</select>
            <input placeholder="Version" value={f.version} onChange={(e) => setF({ ...f, version: e.target.value })} data-testid="disclosure-version" />
            <input type="date" value={f.effective_date} onChange={(e) => setF({ ...f, effective_date: e.target.value })} data-testid="disclosure-effective" />
            {f.kind !== "FSG" ? <>
              <input placeholder="Insurer (as on policies)" value={f.insurer} onChange={(e) => setF({ ...f, insurer: e.target.value })} data-testid="disclosure-insurer" />
              <input placeholder="Product (blank = all)" value={f.product} onChange={(e) => setF({ ...f, product: e.target.value })} data-testid="disclosure-product" />
            </> : null}
            <input type="file" accept=".pdf,application/pdf" onChange={(e) => setFile(e.target.files?.[0] || null)} data-testid="disclosure-file" />
            <button className="k-btn k-btn--primary" disabled={!file || !f.version || !f.effective_date} onClick={upload} data-testid="disclosure-upload-submit">Upload</button>
          </div>
        </div>
      ) : null}
      {err ? <p className="imp-err" role="alert" data-testid="disclosure-error">{err}</p> : null}
      <table className="imp-table" data-testid="disclosure-table">
        <thead><tr><th>Type</th><th>Insurer / product</th><th>Version</th><th>Effective</th><th>SHA-256</th><th>Status</th></tr></thead>
        <tbody>{(data?.documents || []).map((d: any) => (
          <tr key={d.doc_id} data-testid={`doc-row-${d.doc_id}`}>
            <td>{d.kind}</td><td>{d.insurer || "—"}{d.product ? ` · ${d.product}` : ""}</td><td>{d.version}</td><td>{d.effective_date}</td>
            <td className="rn-mono">{d.sha256.slice(0, 12)}…</td><td>{d.active ? "Current" : `Superseded ${day(d.uploaded_at)}`}</td>
          </tr>))}</tbody>
      </table>
    </div>
  );
}
