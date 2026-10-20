/** R1.8 — client claim detail: plain-English timeline, info requests, documents, decision options (IDR/AFCA). */
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { authCall } from "../../lib/authApi";
import { uploadClaimFile } from "./upload";

const API = import.meta.env.VITE_API_BASE || "";
const d = (v?: string | null) => (v ? new Date(v).toLocaleDateString("en-AU") : "—");

function InfoRequest({ cid, r, onDone }: { cid: string; r: any; onDone: () => void }) {
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [err, setErr] = useState<string | null>(null);
  async function send() {
    try {
      if (file) await uploadClaimFile(`/api/c/claims/${cid}`, file, "info_response", false, { req_id: r.req_id });
      await authCall(`c/claims/${cid}/info/${r.req_id}`, { text: text || "File attached." });
      onDone();
    } catch (e: any) { setErr(e.message); }
  }
  return (
    <div className="clm-card clm-card--ask" data-testid={`claim-info-${r.req_id}`}>
      <p><strong>Your broker needs:</strong> {r.items.join(", ")} — by {d(r.due)}</p>
      {r.message ? <p>{r.message}</p> : null}
      <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} data-testid={`claim-info-text-${r.req_id}`} />
      <input type="file" accept="image/*,application/pdf" onChange={(e) => setFile(e.target.files?.[0] || null)} data-testid={`claim-info-file-${r.req_id}`} />
      <button className="brokaid-btn brokaid-btn--primary" disabled={!text && !file} onClick={send} data-testid={`claim-info-send-${r.req_id}`}>Send</button>
      {err ? <p className="brokaid-error">{err}</p> : null}
    </div>
  );
}

function Options({ c, onDone }: { c: any; onDone: () => void }) {
  const [text, setText] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const o = c.options;
  return (
    <div className="clm-card clm-card--decision" data-testid="claim-options">
      <p><strong>What you can do</strong></p>
      <ul>{o.text.map((t: string) => <li key={t}>{t}</li>)}</ul>
      <Link to="/c/complaint" className="brokaid-btn brokaid-btn--secondary" data-testid="claim-make-complaint">Make a complaint</Link>
      <p className="clm-muted" data-testid="claim-afca">{o.afca_text}</p>
      {o.disputed ? <p data-testid="claim-disputed">You've told your broker you disagree. They'll be in touch.</p> : (
        <div>
          <textarea rows={3} placeholder="Tell your broker why you disagree" value={text} onChange={(e) => setText(e.target.value)} data-testid="claim-dispute-text" />
          <button className="brokaid-btn" disabled={!text.trim()} data-testid="claim-dispute-send"
            onClick={() => authCall(`c/claims/${c.claim_id}/dispute`, { text }).then(onDone).catch((e) => setErr(e.message))}>I disagree with this decision</button>
          {err ? <p className="brokaid-error">{err}</p> : null}
        </div>)}
    </div>
  );
}

export function ClaimDetail() {
  const { claim_id = "" } = useParams();
  const [c, setC] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => authCall<any>(`c/claims/${claim_id}`, undefined, "GET").then((x) => setC(x.claim)).catch((e) => setErr(e.message)), [claim_id]);
  useEffect(() => { void load(); }, [load]);
  if (err) return <section className="brokaid-tab"><p className="brokaid-error" data-testid="claim-detail-error">Couldn't load claim.</p></section>;
  if (!c) return <section className="brokaid-tab"><p data-testid="claim-detail-loading">Loading…</p></section>;
  return (
    <section className="brokaid-tab" data-testid="claim-detail-root">
      <header className="brokaid-tab__head">
        <p className="brokaid-tab__eyebrow">Claim · {c.claim_class} {c.subtype}</p>
        <h1 className="brokaid-tab__title" data-testid="claim-detail-ref">{c.claim_reference}</h1>
        <span className={`clm-stage clm-stage--${c.stage}`} data-testid={`claim-detail-status-${c.stage}`}>{c.stage_label}</span>
      </header>
      {c.policy_status && c.policy_status.label !== "In force" ? <p className="clm-status" data-testid="claim-policy-status">{c.policy_status.text}</p> : null}
      <p className="clm-status" data-testid="claim-status-text">{c.status_text}{c.stage === "lodged" ? ` By ${new Date(c.ack_due).toLocaleString("en-AU", { weekday: "short", hour: "numeric", minute: "2-digit" })}.` : ""}</p>
      {c.insurer_claim_no ? <p data-testid="claim-insurer-no">Insurer claim no. {c.insurer_claim_no}{c.insurer_mocked ? " (MOCKED)" : ""}</p> : null}
      {c.excess != null ? <p className="clm-muted">Policy excess A${Number(c.excess).toLocaleString("en-AU")}</p> : null}
      {(c.info_requests || []).filter((r: any) => r.status === "open").map((r: any) => <InfoRequest key={r.req_id} cid={c.claim_id} r={r} onDone={load} />)}
      {c.decision ? <div className="clm-card" data-testid="claim-decision"><p><strong>Insurer's decision: {c.decision.label}</strong>{c.decision.amount ? ` · A$${c.decision.amount}` : ""}</p>
        {c.decision.reason ? <p>{c.decision.reason}</p> : null}</div> : null}
      {c.options ? <Options c={c} onDone={load} /> : null}
      {c.settlement ? <p className="clm-card" data-testid="claim-settlement">Settlement A${c.settlement.amount} (excess A${c.settlement.excess_applied}) to the {c.settlement.payee.replace("_", " ")} — {c.settlement.method}.</p> : null}
      <h2 className="brokaid-claim-timeline__title">Timeline</h2>
      <ol className="brokaid-claim-timeline" data-testid="claim-timeline">
        {c.timeline.map((t: any, i: number) => (
          <li key={i} className="brokaid-claim-timeline__item" data-testid={`claim-timeline-${i}`}>
            <div className="brokaid-claim-timeline__meta"><span className="brokaid-claim-timeline__kind">{t.label}</span><span className="brokaid-claim-timeline__ts">{d(t.at)}</span></div>
            <p className="brokaid-claim-timeline__body">{t.text}</p>
          </li>))}
      </ol>
      {c.files.length ? <><h2 className="brokaid-claim-timeline__title">Documents</h2>
        <ul data-testid="claim-files">{c.files.map((x: any) => <li key={x.file_id}><a href={`${API}/api/c/claims/${c.claim_id}/files/${x.file_id}`} target="_blank" rel="noreferrer" data-testid={`claim-file-${x.file_id}`}>{x.kind.replace("_", " ")}: {x.filename}</a></li>)}</ul></> : null}
      {c.stage !== "closed" && c.stage !== "withdrawn" ? <AddFile cid={c.claim_id} onDone={load} /> : null}
      <Link to="/c/ask" className="brokaid-btn brokaid-btn--secondary" data-testid="claim-detail-ask">Message your broker</Link>
    </section>
  );
}

function AddFile({ cid, onDone }: { cid: string; onDone: () => void }) {
  const [err, setErr] = useState<string | null>(null);
  return (
    <label className="brokaid-field" data-testid="claim-add-file"><span>Add a photo or document</span>
      <input type="file" accept="image/*,application/pdf" data-testid="claim-add-file-input"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) uploadClaimFile(`/api/c/claims/${cid}`, f, f.type === "application/pdf" ? "other" : "photo", false).then(onDone).catch((x) => setErr(x.message)); }} />
      {err ? <span className="brokaid-error">{err}</span> : null}
    </label>
  );
}
