import { useEffect, useState } from "react";
import { API, get } from "./api";

type Field = { key: string; label: string; help: string; max: number };
const LONG = new Set(["address", "classes", "broker_fees", "other_benefits", "staff_pay", "associations", "pi_statement", "complaints_officer", "privacy_officer", "overseas"]);
const AR = new Set(["ar_number", "licensee_name", "licensee_afsl"]);
const SANDBOX_CONFIRM = "This clears the made-up business details and puts all 5 drafts back to blank. Continue?";

// Preview-only (compiled out of production builds): the burmac-sandbox TEST tenant's reset.
function SandboxReset({ onDone, setMsg }: { onDone: () => void; setMsg: (m: string) => void }) {
  const [busy, setBusy] = useState(false);
  async function go() {
    if (!window.confirm(SANDBOX_CONFIRM)) return;
    setBusy(true);
    const r = await fetch(`${API}/api/o/compliance/sandbox/reset`, { method: "POST", credentials: "include" }).catch(() => null);
    setBusy(false);
    if (r?.ok) { setMsg("Sandbox reset: business details cleared and all 5 drafts are blank again."); onDone(); }
    else setMsg(`Reset failed (${r?.status ?? "network"}).`);
  }
  return <button className="k-btn" disabled={busy} onClick={go} data-testid="sandbox-reset">{busy ? "Resetting…" : "Reset sandbox"}</button>;
}

export function BusinessDetails({ role, onSaved }: { role?: string; onSaved: () => void }) {
  const [fields, setFields] = useState<Field[]>([]);
  const [vals, setVals] = useState<Record<string, string>>({});
  const [version, setVersion] = useState(0);
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [sandbox, setSandbox] = useState(false);
  const lead = role === "principal" || role === "operator";
  useEffect(() => {
    if (lead) get<any>("o/compliance/business-details").then((d) => { setFields(d.fields); setVals(d.details.fields); setVersion(d.details.version); setSandbox(!!d.sandbox); }).catch(() => {});
  }, [lead]);
  if (!lead) return null;
  const lt = vals.licence_type || "";
  const shown = fields.filter((f) => (f.key === "afsl_number" ? lt === "afsl" : AR.has(f.key) ? lt === "ar" : true));

  async function save() {
    setMsg(null); setErrs({});
    const r = await fetch(`${API}/api/o/compliance/business-details`, {
      method: "PUT", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(vals),
    });
    const b = await r.json().catch(() => ({}));
    if (r.status === 422 && b.detail?.errors) { setErrs(b.detail.errors); setMsg("Some fields need fixing."); return; }
    if (!r.ok) { setMsg(typeof b.detail === "string" ? b.detail : "Couldn't save."); return; }
    setVersion(b.details.version);
    setMsg(b.regenerated?.length ? `Saved as version ${b.details.version}. ${b.regenerated.length} draft(s) regenerated.` : "No changes to save.");
    onSaved();
  }

  return (
    <div className="imp-card" data-testid="business-details">
      <div className="imp-row">
        <h2 className="imp-h2">Business details</h2>
        <span className="imp-muted" data-testid="business-details-version">{version ? `Version ${version}` : "Not filled in yet"}</span>
        <button className="k-btn" onClick={() => setOpen(!open)} data-testid="business-details-toggle">{open ? "Hide" : "Fill in"}</button>
        {import.meta.env.BROKAID_DEMO_SIGNIN && sandbox ? <SandboxReset onDone={() => { setVals({}); setVersion((v) => v + 1); onSaved(); }} setMsg={setMsg} /> : null}
      </div>
      <p className="imp-muted">These fill the blanks in your draft documents and the licence line on client documents. brokAId checks formats (like the ABN checksum) but can't confirm the details are right — your licensee/compliance adviser should.</p>
      {open ? (
        <div className="bd-grid">
          {shown.map((f) => (
            <label key={f.key} className="bd-field" data-testid={`bd-field-${f.key}`}>
              <span className="bd-label">{f.label}</span>
              {f.key === "licence_type" ? (
                <select value={lt} onChange={(e) => setVals({ ...vals, licence_type: e.target.value })} data-testid="bd-input-licence_type">
                  <option value="">Choose…</option><option value="afsl">We hold our own AFSL</option><option value="ar">We are an authorised representative (AR)</option>
                </select>
              ) : LONG.has(f.key) ? (
                <textarea rows={2} maxLength={f.max} value={vals[f.key] || ""} onChange={(e) => setVals({ ...vals, [f.key]: e.target.value })} data-testid={`bd-input-${f.key}`} />
              ) : (
                <input maxLength={f.max} value={vals[f.key] || ""} onChange={(e) => setVals({ ...vals, [f.key]: e.target.value })} data-testid={`bd-input-${f.key}`} />
              )}
              <span className="imp-muted bd-help">{f.help}</span>
              {errs[f.key] ? <span className="imp-err" role="alert" data-testid={`bd-error-${f.key}`}>{errs[f.key]}</span> : null}
            </label>
          ))}
          <div className="imp-row"><button className="k-btn k-btn--primary" onClick={save} data-testid="business-details-save">Save and regenerate drafts</button></div>
        </div>
      ) : null}
      {msg ? <p className="imp-muted" role="status" data-testid="business-details-msg">{msg}</p> : null}
    </div>
  );
}
