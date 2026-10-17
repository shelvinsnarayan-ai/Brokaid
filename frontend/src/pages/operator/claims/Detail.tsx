/** R1.8 — /o/claims/:id: timeline, checklist, flags, AI summary, documents, notes and stage actions. */
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { API, dt, post, useLoad } from "../compliance/api";
import { uploadClaimFile } from "../../claims/upload";
import { ClaimActions } from "./Actions";
import "../../../styles/import.css";
import "../../../styles/claims.css";

function Notes({ c, onDone }: { c: any; onDone: () => void }) {
  const [text, setText] = useState("");
  const [vis, setVis] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  async function add() {
    try {
      const r = await post<any>(`o/claims/${c.claim_id}/notes`, { text, client_visible: vis });
      if (!r.released) { setMsg(`Not sent: ${r.verdict}${r.reason ? ` — ${r.reason}` : ""}${r.recommended_rewrite ? `. Suggested: ${r.recommended_rewrite}` : ""}`); return; }
      setText(""); setMsg(null); onDone();
    } catch (e: any) { setMsg(e.message); }
  }
  return (
    <div className="imp-card" data-testid="claim-notes">
      <h2>Notes</h2>
      {c.notes.map((n: any) => <p key={n.note_id} data-testid={`claim-note-${n.note_id}`}><strong>{n.client_visible ? "To client" : "Internal"}</strong> · {n.by_name} · {dt(n.at)}<br />{n.text}</p>)}
      <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} data-testid="claim-note-text" />
      <label className="clm-check-row"><input type="checkbox" checked={vis} onChange={(e) => setVis(e.target.checked)} data-testid="claim-note-client-visible" />Client can see this (runs the coverage guard)</label>
      <button className="k-btn" disabled={!text.trim()} onClick={add} data-testid="claim-note-add">Add note</button>
      {msg ? <p className="imp-err" role="alert" data-testid="claim-note-msg">{msg}</p> : null}
    </div>
  );
}

function Files({ c, onDone }: { c: any; onDone: () => void }) {
  const [kind, setKind] = useState("photo");
  const [err, setErr] = useState<string | null>(null);
  return (
    <div className="imp-card" data-testid="claim-files">
      <h2>Documents</h2>
      {c.files.map((d: any) => <p key={d.file_id}><a href={`${API}/api/o/claims/${c.claim_id}/files/${d.file_id}`} target="_blank" rel="noreferrer" data-testid={`claim-file-${d.file_id}`}>{d.kind.replace("_", " ")}: {d.filename}</a> <span className="imp-muted">{d.uploaded_role} · {dt(d.uploaded_at)}{d.location_kept ? " · location kept" : ""}</span></p>)}
      {!c.files.length ? <p className="imp-muted">None yet.</p> : null}
      <div className="clm-o-filters">
        <select value={kind} onChange={(e) => setKind(e.target.value)} data-testid="claim-file-kind">
          {["photo", "quote", "invoice", "police_report", "decision_letter", "other"].map((k) => <option key={k}>{k}</option>)}
        </select>
        <input type="file" accept="image/*,application/pdf" data-testid="claim-file-file"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) uploadClaimFile(`/api/o/claims/${c.claim_id}`, f, kind, false).then(onDone).catch((x) => setErr(x.message)); }} />
      </div>
      {err ? <p className="imp-err" role="alert">{err}</p> : null}
    </div>
  );
}

function Summary({ c, onDone }: { c: any; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const s = c.ai_summary;
  return (
    <div className="imp-card" data-testid="claim-ai">
      <h2>AI summary</h2>
      <p className="imp-muted">A plain summary of what the client said. It makes no coverage decision. Check it against the client's words.</p>
      {s?.text ? <p className="clm-ai" data-testid="claim-ai-text"><strong>AI summary</strong>{"\n"}{s.text}</p> : null}
      {s?.withheld ? <p className="imp-err" data-testid="claim-ai-withheld">Summary withheld by the safety check: {s.withheld.join("; ")}</p> : null}
      <button className="k-btn" disabled={busy} data-testid="claim-ai-run"
        onClick={() => { setBusy(true); post(`o/claims/${c.claim_id}/ai-summary`, {}).then(onDone).catch((e) => setErr(e.message)).finally(() => setBusy(false)); }}>
        {busy ? "Summarising…" : s ? "Summarise again" : "Summarise incident"}</button>
      {err ? <p className="imp-err" role="alert">{err}</p> : null}
    </div>
  );
}

export function OperatorClaim() {
  const { claim_id = "" } = useParams();
  const { data, err, load } = useLoad<any>(`o/claims/${claim_id}`);
  if (err) return <p className="imp-err" data-testid="claim-missing">{err} <Link to="/o/claims">Back to claims</Link></p>;
  const c = data?.claim;
  if (!c) return <p className="imp-muted" data-testid="claim-loading">Loading…</p>;
  const i = c.incident;
  return (
    <section className="imp-page" data-testid="operator-claim-root">
      <Link to="/o/claims" className="imp-muted" data-testid="claim-back">← Claims</Link>
      <header>
        <p className="imp-muted">CLAIM · {c.client_name} · {c.channel}{c.test ? " · TEST" : ""}</p>
        <h1 data-testid="claim-ref">{c.claim_reference}</h1>
        <p data-testid="claim-stage"><strong>{c.stage_label}</strong>{c.overdue ? " · OVERDUE" : ""} · acknowledge by {dt(c.ack_due)} · {c.product || "no policy"} · {c.insurer || "—"} · excess {c.excess ?? "—"}</p>
      </header>
      <div className="clm-o-grid">
        <div className="imp-page">
          <div className="imp-card" data-testid="claim-incident">
            <h2>Incident</h2>
            <p>{c.claim_class} · {c.subtype} · {i.date || "date not given"} {i.time || ""} · {i.where || "place not given"}{i.rego ? ` · ${i.rego}` : ""}</p>
            <p style={{ whiteSpace: "pre-wrap" }}>{i.what}</p>
            <p className="imp-muted">Injuries: {i.injuries == null ? "—" : i.injuries ? "yes" : "no"} · safe/drivable: {i.safe == null ? "—" : i.safe ? "yes" : "no"} · police report: {i.police_report_no || "—"}</p>
            {c.parties?.map((p: any, k: number) => <p key={k} className="imp-muted">Other party: {p.name || "—"} · {p.rego || "—"} · {p.insurer || "—"}</p>)}
          </div>
          <ClaimActions c={c} quotingMode={data.quoting_mode} onDone={load} />
          <Summary c={c} onDone={load} />
          <Files c={c} onDone={load} />
          <Notes c={c} onDone={load} />
        </div>
        <div className="imp-page">
          <div className="imp-card" data-testid="claim-flags">
            <h2>Flags for you</h2>
            {c.flags.length ? c.flags.map((f: any) => <p key={f.code} className="clm-o-flag" data-testid={`claim-flag-${f.code}`}>{f.text}</p>) : <p className="imp-muted">No flags.</p>}
            <button className="k-btn" onClick={() => post(`o/claims/${c.claim_id}/flags/refresh`, {}).then(load)} data-testid="claim-flags-refresh">Re-check</button>
          </div>
          <div className="imp-card" data-testid="claim-checklist">
            <h2>Completeness</h2>
            {c.checklist.map((x: any) => <p key={x.key} data-testid={`claim-check-${x.key}`}>{x.done ? "✓" : "○"} {x.label}</p>)}
          </div>
          <div className="imp-card" data-testid="claim-timeline">
            <h2>Timeline</h2>
            {c.timeline.map((t: any, k: number) => <p key={k}><strong>{t.label}</strong> · {dt(t.at)}{t.note ? ` — ${t.note}` : ""}</p>)}
            {c.info_requests.map((r: any) => <p key={r.req_id} className="imp-muted" data-testid={`claim-req-${r.req_id}`}>Asked for {r.items.join(", ")} by {r.due} — {r.status}{r.answer ? `: ${r.answer.text}` : ""}</p>)}
          </div>
        </div>
      </div>
    </section>
  );
}
