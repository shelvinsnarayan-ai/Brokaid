/** R2.0 — click-to-accept: the exact document, a tick, the client's typed name. Not a digital signature. */
import { useCallback, useEffect, useState } from "react";
import "../styles/acceptance.css";

const API = import.meta.env.VITE_API_BASE || "";

type Doc = { title: string; lines: [string, string][]; doc_sha256: string; ready: boolean; label: string; accepted: any };

export function AcceptPanel({ kind, refId, onDone }: { kind: "endorsement" | "renewal"; refId: string; onDone?: (r: any) => void }) {
  const base = `${API}/api/c/acceptance/${kind}/${refId}`;
  const [doc, setDoc] = useState<Doc | null>(null);
  const [name, setName] = useState("");
  const [tick, setTick] = useState(false);
  const [mismatch, setMismatch] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => {
    fetch(base, { credentials: "include" }).then(async (r) => (r.ok ? setDoc(await r.json()) : setErr("We couldn't load the document.")));
  }, [base]);
  useEffect(load, [load]);

  async function accept(confirm_name = false) {
    if (!doc) return;
    setBusy(true); setErr(null);
    const r = await fetch(base, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ typed_name: name, accepted: tick, doc_sha256: doc.doc_sha256, confirm_name }) });
    const body = await r.json().catch(() => ({}));
    setBusy(false);
    if (r.status === 409 && body?.detail?.code === "name_mismatch") { setMismatch(true); return; }
    if (!r.ok) { setErr(typeof body.detail === "string" ? body.detail : r.status === 403 ? "Only someone allowed to accept for this business can do this." : "That didn't work — please try again."); if (r.status === 409) load(); return; }
    setMismatch(false);
    load();
    onDone?.(body);
  }

  if (!doc) return <div className="acc-card" data-testid="accept-loading">{err || "Loading the document…"}</div>;
  const done = doc.accepted;
  return (
    <article className="acc-card" data-testid="accept-panel">
      <header className="acc-head">
        <p className="acc-eyebrow">{doc.label}</p>
        <h2 className="acc-title" data-testid="accept-doc-title">{doc.title}</h2>
      </header>
      <dl className="acc-lines" data-testid="accept-doc-lines">
        {doc.lines.map(([k, v], i) => <div key={i} className="acc-line"><dt>{k}</dt><dd>{v}</dd></div>)}
      </dl>
      <p className="acc-hash">Document SHA-256 <code data-testid="accept-doc-hash">{doc.doc_sha256}</code></p>
      <a className="acc-link" href={`${base}/pdf`} data-testid="accept-doc-download">Download the PDF</a>
      {done ? (
        <div className="acc-receipt" data-testid="accept-receipt">
          <p><b>{done.label}</b></p>
          <p>{done.typed_name ? `${done.typed_name} · ` : ""}{done.accepted_at_sydney || done.accepted_at_utc}</p>
          <p className="acc-hash">Recorded against document <code data-testid="accept-receipt-hash">{done.doc_sha256}</code></p>
        </div>
      ) : !doc.ready ? (
        <p className="acc-note" data-testid="accept-not-ready">This opens once the earlier steps are complete.</p>
      ) : (
        <div className="acc-form">
          <label className="acc-tick">
            <input type="checkbox" checked={tick} onChange={(e) => setTick(e.target.checked)} data-testid="accept-tick" />
            <span>I've read and accept</span>
          </label>
          <label className="acc-name">
            <span>Type your full name</span>
            <input value={name} onChange={(e) => { setName(e.target.value); setMismatch(false); }} autoComplete="name" data-testid="accept-name" />
          </label>
          {mismatch ? (
            <div className="acc-warn" role="alert" data-testid="accept-name-mismatch">
              <p>That name doesn't match the name we hold for you. Check the spelling, or confirm it's correct.</p>
              <button className="k-btn" disabled={busy} onClick={() => accept(true)} data-testid="accept-confirm-name">Yes, this is my name</button>
            </div>
          ) : null}
          {err ? <p className="brokaid-form__err" role="alert" data-testid="accept-error">{err}</p> : null}
          <button className="brokaid-cta" disabled={!tick || name.trim().length < 2 || busy} onClick={() => accept(false)} data-testid="accept-submit">
            {busy ? "Recording…" : "Accept"}</button>
          <p className="acc-note">This is recorded as “{doc.label}”.</p>
        </div>
      )}
    </article>
  );
}
