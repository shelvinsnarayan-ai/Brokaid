import { useState } from "react";
import { API, day, post, useLoad } from "./api";

const STATUS: Record<string, string> = { draft: "Draft", approved: "Approved by Principal", live: "Live", obtain_from_insurer: "Obtain from insurer" };

export function Library({ role }: { role?: string }) {
  const { data, err, load, setErr } = useLoad<any>("o/compliance/library");
  const lead = role === "principal" || role === "operator";
  const run = (p: Promise<any>) => p.then(load).catch((e) => setErr(e.message));
  return (
    <div className="imp-page" data-testid="library-tab">
      <div className="imp-card">
        <h2 className="imp-h2">Compliance document library</h2>
        <p className="imp-muted">Drafts are templates for review by your licensee/compliance adviser. Nothing counts for go-live until a Principal approves it and it is made live. PDS and TMD come from each insurer.</p>
        {lead ? <div className="imp-row">
          <button className="k-btn" onClick={() => run(post("o/compliance/library/drafts", {}))} data-testid="library-generate">Generate draft templates</button>
          <a className="k-btn" href={`${API}/api/o/compliance/library/drafts.zip`} data-testid="library-download-all">Download all drafts (DOCX + PDF)</a>
        </div> : null}
      </div>
      {err ? <p className="imp-err" role="alert" data-testid="library-error">{err}</p> : null}
      <table className="imp-table" data-testid="library-table">
        <thead><tr><th>Document</th><th>Status</th><th>Version</th><th>Placeholders</th><th>Files</th><th /></tr></thead>
        <tbody>{(data?.documents || []).map((d: any) => <Row key={d.doc_id || d.kind} d={d} role={role} confirm={data?.confirmation} onDone={load} setErr={setErr} />)}</tbody>
      </table>
    </div>
  );
}

function Row({ d, role, confirm, onDone, setErr }: any) {
  const [name, setName] = useState("");
  const [tick, setTick] = useState(false);
  const id = d.doc_id || d.kind;
  if (d.insurer_issued) return <tr data-testid={`library-row-${d.kind}`}><td>{d.title}</td><td>{STATUS[d.status]}</td><td colSpan={4} className="imp-muted">Issued by the insurer — upload each insurer's current version under Documents.</td></tr>;
  const act = (p: Promise<any>) => p.then(onDone).catch((e) => setErr(e.message));
  async function final(file: File) {
    const fd = new FormData(); fd.append("file", file);
    const r = await fetch(`${API}/api/o/compliance/library/${d.doc_id}/final`, { method: "POST", body: fd, credentials: "include" });
    const b = await r.json().catch(() => ({}));
    if (!r.ok) setErr(typeof b.detail === "string" ? b.detail : "Upload failed"); else onDone();
  }
  return (
    <tr data-testid={`library-row-${d.kind}`}>
      <td>{d.title}</td>
      <td data-testid={`library-status-${d.kind}`}>{STATUS[d.status] || d.status}{d.approval ? ` · ${d.approval.reviewer_name} ${day(d.approval.at)}` : ""}</td>
      <td>{d.version}</td>
      <td data-testid={`library-placeholders-${d.kind}`}>{d.placeholders || "—"}</td>
      <td>
        <a href={`${API}/api/o/compliance/library/${id}/download?fmt=docx`} data-testid={`library-docx-${d.kind}`}>DOCX</a>{" · "}
        <a href={`${API}/api/o/compliance/library/${id}/download?fmt=pdf`} data-testid={`library-pdf-${d.kind}`}>PDF</a>
        {d.final_sha256 ? <>{" · "}<a href={`${API}/api/o/compliance/library/${id}/download?fmt=final`} data-testid={`library-final-${d.kind}`}>Final</a></> : null}
      </td>
      <td>
        {d.status === "draft" && d.kind === "FSG" && (role === "principal" || role === "operator") ? (
          <label className="k-btn">Upload final PDF<input hidden type="file" accept="application/pdf" onChange={(e) => e.target.files?.[0] && final(e.target.files[0])} data-testid="library-final-upload" /></label>) : null}
        {d.status === "draft" && role === "principal" ? (
          <span className="imp-row">
            <input placeholder="Reviewer name" value={name} onChange={(e) => setName(e.target.value)} data-testid={`library-reviewer-${d.kind}`} />
            <label><input type="checkbox" checked={tick} onChange={(e) => setTick(e.target.checked)} data-testid={`library-confirm-${d.kind}`} /> {confirm}</label>
            <button className="k-btn k-btn--primary" disabled={!tick || name.trim().length < 2}
              onClick={() => act(post(`o/compliance/library/${d.doc_id}/approve`, { reviewer_name: name, confirmed: tick }))} data-testid={`library-approve-${d.kind}`}>Approve</button>
          </span>) : null}
        {d.status === "approved" && (role === "principal" || role === "operator") ? (
          <button className="k-btn k-btn--primary" onClick={() => act(post(`o/compliance/library/${d.doc_id}/live`, {}))} data-testid={`library-live-${d.kind}`}>Make live</button>) : null}
      </td>
    </tr>
  );
}
