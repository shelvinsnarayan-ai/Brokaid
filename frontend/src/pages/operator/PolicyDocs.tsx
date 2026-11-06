/** R3.13 — staff: upload/replace/withdraw a client's schedule, PDS or wording (AE/Principal; Operator read-only). */
import { useCallback, useEffect, useState } from "react";

const API = import.meta.env.VITE_API_BASE || "";

interface Doc { doc_id: string; kind: string; kind_label: string; term_label: string; current: boolean; sample: boolean; filename: string;
  created_at: string | null; withdrawn: boolean; withdrawn_reason: string | null }
interface Pol { policy_id: string; product: string; insurer_display?: string; documents: Doc[] }

function UploadForm({ pid, replaces, onDone }: { pid: string; replaces?: Doc; onDone: () => void }) {
  const [kind, setKind] = useState(replaces?.kind || "schedule");
  const [term, setTerm] = useState(replaces?.term_label || "");
  const [current, setCurrent] = useState(replaces ? replaces.current : true);
  const [file, setFile] = useState<File | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const send = async () => {
    if (!file) return;
    setBusy(true); setErr(null);
    const fd = new FormData();
    fd.append("file", file); fd.append("kind", kind); fd.append("term_label", term); fd.append("current", String(current));
    if (replaces) fd.append("replaces", replaces.doc_id);
    const r = await fetch(`${API}/api/o/policies/${pid}/documents`, { method: "POST", credentials: "include", body: fd });
    const b = await r.json().catch(() => ({}));
    setBusy(false);
    if (!r.ok) { setErr(typeof b.detail === "string" ? b.detail : "Upload failed."); return; }
    setFile(null); setTerm("");
    (document.querySelector(`[data-testid="pdoc-file-${pid}"]`) as HTMLInputElement | null)?.value && ((document.querySelector(`[data-testid="pdoc-file-${pid}"]`) as HTMLInputElement).value = "");
    onDone();
    window.dispatchEvent(new Event("brokaid:timeline-refresh"));
  };
  return (
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end", margin: "8px 0" }} data-testid={`pdoc-form-${pid}`}>
      {!replaces ? (
        <select value={kind} onChange={(e) => setKind(e.target.value)} className="k-input" data-testid={`pdoc-kind-${pid}`}>
          <option value="schedule">Policy schedule</option><option value="pds">PDS</option><option value="wording">Policy wording</option>
        </select>
      ) : null}
      {kind === "schedule" ? <>
        <input className="k-input" placeholder="Term, e.g. 22 Jun 2026 – 22 Jun 2027" value={term} onChange={(e) => setTerm(e.target.value)} data-testid={`pdoc-term-${pid}`} />
        <label style={{ fontSize: 12 }}><input type="checkbox" checked={current} onChange={(e) => setCurrent(e.target.checked)} data-testid={`pdoc-current-${pid}`} /> Current term</label>
      </> : null}
      <input type="file" accept="application/pdf" className="k-file" onChange={(e) => setFile(e.target.files?.[0] || null)} data-testid={`pdoc-file-${pid}`} />
      <button type="button" className="k-btn k-btn--primary" disabled={!file || busy} onClick={send} data-testid={`pdoc-upload-${pid}`}>
        {busy ? "Uploading…" : replaces ? "Replace" : "Upload PDF"}</button>
      {err ? <span className="imp-err" role="alert" data-testid={`pdoc-error-${pid}`}>{err}</span> : null}
    </div>
  );
}

function DocRow({ d, pid, edit, reload }: { d: Doc; pid: string; edit: boolean; reload: () => void }) {
  const [replacing, setReplacing] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const withdraw = async () => {
    const reason = window.prompt("Why are you withdrawing this document? (the client stops seeing it; it's kept in the record)");
    if (reason === null) return;
    if (reason.trim().length < 3) { setErr("Give a short reason (at least 3 characters)."); return; }
    const r = await fetch(`${API}/api/o/policy-documents/${d.doc_id}/withdraw`, { method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason: reason.trim() }) });
    if (!r.ok) { const b = await r.json().catch(() => ({})); setErr(typeof b.detail === "string" ? b.detail : "Couldn't withdraw it."); return; }
    setErr(null);
    reload();
  };
  return (
    <li style={{ padding: "6px 0", opacity: d.withdrawn ? 0.55 : 1 }} data-testid={`pdoc-row-${d.doc_id}`}>
      <a href={`${API}/api/o/policy-documents/${d.doc_id}/file`} target="_blank" rel="noreferrer">{d.kind_label}</a>
      {d.term_label ? ` · ${d.term_label}` : ""}{d.kind === "schedule" && !d.withdrawn ? (d.current ? " · current" : " · previous") : ""}
      {d.sample ? " · SAMPLE" : ""}{d.withdrawn ? ` · withdrawn (${d.withdrawn_reason})` : ""}
      {err ? <span className="imp-err" role="alert" data-testid={`pdoc-row-error-${d.doc_id}`}> {err}</span> : null}
      {edit && !d.withdrawn ? <>
        {" "}<button type="button" className="k-btn" onClick={() => setReplacing(!replacing)} data-testid={`pdoc-replace-${d.doc_id}`}>Replace</button>
        {" "}<button type="button" className="k-btn" onClick={withdraw} data-testid={`pdoc-withdraw-${d.doc_id}`}>Withdraw</button>
      </> : null}
      {replacing ? <UploadForm pid={pid} replaces={d} onDone={() => { setReplacing(false); reload(); }} /> : null}
    </li>
  );
}

export function PolicyDocs({ clientId }: { clientId: string }) {
  const [d, setD] = useState<{ can_edit: boolean; policies: Pol[] } | null>(null);
  const load = useCallback(() => {
    fetch(`${API}/api/o/clients/${clientId}/policy-documents`, { credentials: "include" }).then((r) => (r.ok ? r.json() : null)).then(setD);
  }, [clientId]);
  useEffect(load, [load]);
  if (!d) return null;
  return (
    <section style={{ marginTop: 20 }} data-testid="pdoc-section">
      <h2 style={{ fontFamily: "var(--font-display-ledger)", fontWeight: 300, fontSize: 18 }}>Schedules and PDS</h2>
      {!d.can_edit ? <p className="imp-muted" data-testid="pdoc-readonly">Read-only for your role. An account executive or Principal can upload.</p> : null}
      {d.policies.map((p) => (
        <details key={p.policy_id} className="staff-playbook" data-testid={`pdoc-policy-${p.policy_id}`}>
          <summary>{p.product} · {p.documents.filter((x) => !x.withdrawn).length} document(s)</summary>
          <ul style={{ listStyle: "none", padding: 0, margin: "8px 0" }}>
            {p.documents.map((x) => <DocRow key={x.doc_id} d={x} pid={p.policy_id} edit={d.can_edit} reload={load} />)}
          </ul>
          {d.can_edit ? <UploadForm pid={p.policy_id} onDone={load} /> : null}
        </details>
      ))}
    </section>
  );
}
