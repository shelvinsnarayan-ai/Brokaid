/** R1.7 — client: documents provided, complaints, My data (APP access / correction / privacy complaint). */
import { useCallback, useEffect, useState } from "react";
import { authCall } from "../lib/authApi";

const API = import.meta.env.VITE_API_BASE || "";
const day = (v?: string) => (v ? new Date(v).toLocaleDateString("en-AU") : "—");

function useGet(path: string) {
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => { authCall(path, undefined, "GET").then(setD).catch((e) => setErr(e.message)); }, [path]);
  useEffect(load, [load]);
  return { d, err, setErr, load };
}

function Head({ eyebrow, title, lede }: { eyebrow: string; title: string; lede: string }) {
  return (
    <header className="brokaid-tab__head">
      <p className="brokaid-tab__eyebrow">{eyebrow}</p><h1 className="brokaid-tab__title">{title}</h1><p className="brokaid-tab__lede">{lede}</p>
    </header>
  );
}

export function ClientDocuments() {
  const { d, err } = useGet("c/documents");
  return (
    <section className="brokaid-tab rn-c-page" data-testid="client-documents">
      <Head eyebrow="You" title="Documents provided to you" lede="Every disclosure document we've given you, with the version and date." />
      {err ? <p className="brokaid-form__err" role="alert">{err}</p> : null}
      {d && !d.documents.length ? <p data-testid="client-documents-empty">Nothing yet.</p> : null}
      {(d?.documents || []).map((x: any) => (
        <a key={x.delivery_id} className="rn-c-card" href={`${API}/api/c/documents/download?d=${x.delivery_id}`} data-testid={`client-doc-${x.delivery_id}`}>
          <span className="rn-c-card__eyebrow">{x.kind === "FSG" ? "Financial Services Guide" : x.kind === "PDS" ? "Product Disclosure Statement" : x.kind === "TMD" ? "Target Market Determination" : "Policy wording"}</span>
          <span>{x.insurer ? `${x.insurer} · ` : ""}version {x.version} · provided {day(x.at)}</span>
          <span className="brokaid-next-action__cta">Download →</span>
        </a>
      ))}
    </section>
  );
}

export function ClientComplaint() {
  const { d, err, setErr, load } = useGet("c/complaints");
  const [f, setF] = useState({ category: "service", summary: "", details: "" });
  const [done, setDone] = useState<any>(null);
  async function send() {
    setErr(null);
    try { setDone(await authCall("c/complaints", f)); setF({ ...f, summary: "", details: "" }); load(); } catch (e: any) { setErr(e.message); }
  }
  return (
    <section className="brokaid-tab rn-c-page" data-testid="client-complaint">
      <Head eyebrow="You" title="Make a complaint" lede="Tell us what went wrong. We'll acknowledge it within 1 business day and give you our final response within 30 days." />
      <div className="rn-c-q">
        <select value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })} data-testid="client-complaint-category">
          {(d?.categories || ["service"]).map((c: string) => <option key={c} value={c}>{c.replace(/_/g, " ")}</option>)}</select>
        <input placeholder="What's it about?" value={f.summary} onChange={(e) => setF({ ...f, summary: e.target.value })} data-testid="client-complaint-summary" />
        <textarea rows={4} placeholder="What happened, and what would put it right?" value={f.details} onChange={(e) => setF({ ...f, details: e.target.value })} data-testid="client-complaint-details" />
        <button className="brokaid-cta" disabled={!f.summary.trim()} onClick={send} data-testid="client-complaint-submit">Send complaint</button>
      </div>
      {done ? <p data-testid="client-complaint-done">Received. We'll acknowledge it by {new Date(done.ack_due).toLocaleString("en-AU")} and respond by {day(done.response_due)}.</p> : null}
      {err ? <p className="brokaid-form__err" role="alert" data-testid="client-complaint-error">{err}</p> : null}
      {(d?.complaints || []).map((m: any) => (
        <div key={m.matter_id} className="rn-c-card" data-testid={`client-complaint-${m.matter_id}`}>
          <span className="rn-c-card__eyebrow">{day(m.received_at)} · {m.stage_label}</span><span>{m.summary}</span>
          {m.response_text ? <span style={{ whiteSpace: "pre-wrap" }}>{m.response_text}</span> : <span className="rn-c-warn">Response due by {day(m.response_due)}</span>}
        </div>
      ))}
      {d ? <p className="rn-c-warn" data-testid="client-complaint-afca">{d.afca_text}</p> : null}
    </section>
  );
}

export function ClientMyData() {
  const { d, err, setErr, load } = useGet("c/my-data");
  const [details, setDetails] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  async function ask(kind: string) {
    setErr(null);
    try {
      const r = await authCall("c/my-data/requests", { kind, details: details || null });
      setMsg(kind === "access" ? "We're preparing your copy. It'll appear here shortly and the link lasts 7 days." : `Received. We'll respond by ${day(r.response_due)}.`);
      setDetails(""); setTimeout(load, 4000);
    } catch (e: any) { setErr(e.message); }
  }
  return (
    <section className="brokaid-tab rn-c-page" data-testid="client-my-data">
      <Head eyebrow="You" title="My data" lede="Get a copy of the personal information we hold, ask us to correct it, or make a privacy complaint. We respond within 30 days." />
      <button className="brokaid-cta" onClick={() => ask("access")} data-testid="my-data-access">Get a copy of my data</button>
      <textarea rows={3} placeholder="What needs correcting, or what went wrong?" value={details} onChange={(e) => setDetails(e.target.value)} data-testid="my-data-details" />
      <div className="brokaid-form__row">
        <button className="k-btn" disabled={!details.trim()} onClick={() => ask("correction")} data-testid="my-data-correction">Ask for a correction</button>
        <button className="k-btn" disabled={!details.trim()} onClick={() => ask("complaint")} data-testid="my-data-complaint">Privacy complaint</button>
      </div>
      {msg ? <p data-testid="my-data-msg">{msg}</p> : null}
      {err ? <p className="brokaid-form__err" role="alert">{err}</p> : null}
      {(d?.exports || []).map((e: any) => (
        <div key={e.export_id} className="rn-c-card" data-testid={`my-data-export-${e.export_id}`}>
          <span className="rn-c-card__eyebrow">Copy of your data · {day(e.created_at)} · {e.expired ? "link expired" : `link valid until ${day(e.expires_at)}`}</span>
          {!e.expired ? <span><a href={`${API}/api/c/my-data/export?e=${e.export_id}&fmt=pdf`} data-testid={`my-data-pdf-${e.export_id}`}>Summary (PDF)</a> · <a href={`${API}/api/c/my-data/export?e=${e.export_id}&fmt=json`} data-testid={`my-data-json-${e.export_id}`}>Everything (JSON)</a></span> : null}
        </div>
      ))}
      {(d?.requests || []).map((m: any) => (
        <p key={m.matter_id} data-testid={`my-data-request-${m.matter_id}`}>{day(m.received_at)} · {m.category} · {m.stage_label} · due {day(m.response_due)}</p>
      ))}
      {d ? <details data-testid="my-data-policy"><summary>Privacy policy and collection notice</summary><p>{d.privacy_policy_text}</p><p>{d.collection_notice_text}</p></details> : null}
    </section>
  );
}
