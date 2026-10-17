/** R1.8 — guided claim form by policy class. Flags (not decisions) come back from the policy check. */
import { FormEvent, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { authCall } from "../../lib/authApi";
import { uploadClaimFile } from "./upload";

const SUBTYPES: Record<string, string[]> = { motor: ["accident", "theft", "glass", "hail"], property: ["damage"], liability: ["third_party"], other: ["other"] };
const CLASS_LABEL: Record<string, string> = { motor: "Motor", property: "Property damage", liability: "Liability", other: "Other" };

function YesNo({ label, v, set, id }: { label: string; v: boolean | null; set: (b: boolean) => void; id: string }) {
  return (
    <fieldset className="clm-yesno" data-testid={id}>
      <legend>{label}</legend>
      <button type="button" className={v === true ? "on" : ""} onClick={() => set(true)} data-testid={`${id}-yes`}>Yes</button>
      <button type="button" className={v === false ? "on" : ""} onClick={() => set(false)} data-testid={`${id}-no`}>No</button>
    </fieldset>
  );
}

function PolicyCheck({ policyId, date, rego, cls, onVehicles }: { policyId: string; date: string; rego: string; cls: string; onVehicles: (v: any[]) => void }) {
  const [c, setC] = useState<any>(null);
  useEffect(() => {
    if (!policyId) return;
    const q = new URLSearchParams({ policy_id: policyId, claim_class: cls, ...(date ? { incident_date: date } : {}), ...(rego ? { rego } : {}) });
    authCall(`c/claims/check?${q}`, undefined, "GET").then((d) => { setC(d); onVehicles(d.vehicles || []); }).catch(() => setC(null));
  }, [policyId, date, rego, cls]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!c) return null;
  return (
    <div className="clm-check" data-testid="fnol-policy-check">
      {c.excess != null ? <p data-testid="fnol-excess">Excess on this policy: <strong>A${Number(c.excess).toLocaleString("en-AU")}</strong></p> : null}
      {(c.flags || []).map((f: any) => <p key={f.code} className="clm-flag" data-testid={`fnol-flag-${f.code}`}>{f.text}</p>)}
      <p className="clm-muted">{c.note}</p>
    </div>
  );
}

export function ClaimsNew() {
  const nav = useNavigate();
  const [policies, setPolicies] = useState<any[]>([]);
  const [vehicles, setVehicles] = useState<any[]>([]);
  const [f, setF] = useState({ policy_id: "", claim_class: "motor", subtype: "accident", incident_date: "", incident_time: "", where: "",
    description: "", police_report_no: "", rego: "", party_name: "", party_rego: "", party_insurer: "" });
  const [injuries, setInjuries] = useState<boolean | null>(null);
  const [safe, setSafe] = useState<boolean | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [keepLoc, setKeepLoc] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const set = (k: string, v: string) => setF((x) => ({ ...x, [k]: v }));
  useEffect(() => { authCall<any>("c/policies", undefined, "GET").then((d) => setPolicies((d.policies || []).filter((p: any) => p.status === "managed"))).catch(() => {}); }, []);
  const motor = f.claim_class === "motor";
  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setErr(null);
    try {
      const parties = f.party_name || f.party_rego || f.party_insurer ? [{ name: f.party_name, rego: f.party_rego, insurer: f.party_insurer }] : [];
      const r = await authCall<any>("c/claims", { policy_id: f.policy_id || null, claim_class: f.claim_class, subtype: f.subtype,
        incident_date: f.incident_date || null, incident_time: f.incident_time || null, where: f.where, description: f.description,
        police_report_no: f.police_report_no || null, rego: motor ? f.rego || null : null, injuries, safe, parties });
      for (const file of files) await uploadClaimFile(`/api/c/claims/${r.claim_id}`, file, file.type === "application/pdf" ? "other" : "photo", keepLoc);
      nav(`/c/claims/${r.claim_id}`, { replace: true });
    } catch (x: any) { setErr(x.message); } finally { setBusy(false); }
  }
  return (
    <section className="brokaid-tab" data-testid="claims-new-root">
      <header className="brokaid-tab__head"><p className="brokaid-tab__eyebrow">Claims / New</p><h1 className="brokaid-tab__title">Make a claim</h1></header>
      <form className="brokaid-fnol" onSubmit={submit} data-testid="fnol-form">
        <label className="brokaid-field"><span>Which policy?</span>
          <select value={f.policy_id} onChange={(e) => set("policy_id", e.target.value)} data-testid="fnol-policy">
            <option value="">Not sure</option>
            {policies.map((p) => <option key={p.policy_id} value={p.policy_id}>{p.product} · {p.insurer_display}</option>)}
          </select></label>
        <div className="clm-chips" data-testid="fnol-class">
          {Object.keys(SUBTYPES).map((k) => <button type="button" key={k} className={f.claim_class === k ? "on" : ""} data-testid={`fnol-class-${k}`}
            onClick={() => setF((x) => ({ ...x, claim_class: k, subtype: SUBTYPES[k][0] }))}>{CLASS_LABEL[k]}</button>)}
        </div>
        {motor ? <div className="clm-chips" data-testid="fnol-subtype">
          {SUBTYPES.motor.map((k) => <button type="button" key={k} className={f.subtype === k ? "on" : ""} onClick={() => set("subtype", k)} data-testid={`fnol-subtype-${k}`}>{k}</button>)}
        </div> : null}
        {motor ? <label className="brokaid-field"><span>Which vehicle?</span>
          <select value={f.rego} onChange={(e) => set("rego", e.target.value)} data-testid="fnol-vehicle">
            <option value="">Choose…</option>
            {vehicles.map((v) => <option key={v.rego} value={v.rego}>{v.label}</option>)}
          </select></label> : null}
        <div className="clm-row">
          <label className="brokaid-field"><span>When did it happen?</span><input type="date" value={f.incident_date} onChange={(e) => set("incident_date", e.target.value)} data-testid="fnol-incident-date" /></label>
          <label className="brokaid-field"><span>Time (if known)</span><input type="time" value={f.incident_time} onChange={(e) => set("incident_time", e.target.value)} data-testid="fnol-incident-time" /></label>
        </div>
        <label className="brokaid-field"><span>Where?</span><input value={f.where} onChange={(e) => set("where", e.target.value)} data-testid="fnol-where" /></label>
        <label className="brokaid-field"><span>What happened?</span>
          <textarea required minLength={8} rows={6} value={f.description} onChange={(e) => set("description", e.target.value)} data-testid="fnol-description" /></label>
        {f.claim_class !== "property" ? <div className="clm-row">
          <label className="brokaid-field"><span>Other party name</span><input value={f.party_name} onChange={(e) => set("party_name", e.target.value)} data-testid="fnol-party-name" /></label>
          <label className="brokaid-field"><span>Their rego</span><input value={f.party_rego} onChange={(e) => set("party_rego", e.target.value)} data-testid="fnol-party-rego" /></label>
          <label className="brokaid-field"><span>Their insurer</span><input value={f.party_insurer} onChange={(e) => set("party_insurer", e.target.value)} data-testid="fnol-party-insurer" /></label>
        </div> : null}
        <label className="brokaid-field"><span>Police report no. (if any)</span><input value={f.police_report_no} onChange={(e) => set("police_report_no", e.target.value)} data-testid="fnol-police" /></label>
        <YesNo label="Was anyone injured?" v={injuries} set={setInjuries} id="fnol-injuries" />
        <YesNo label={motor ? "Is the vehicle safe to drive?" : "Is everything safe now?"} v={safe} set={setSafe} id="fnol-safe" />
        <label className="brokaid-field"><span>Photos, quotes, invoices, police report (10 MB each)</span>
          <input type="file" multiple accept="image/*,application/pdf" onChange={(e) => setFiles(Array.from(e.target.files || []))} data-testid="fnol-files" /></label>
        <label className="clm-check-row"><input type="checkbox" checked={keepLoc} onChange={(e) => setKeepLoc(e.target.checked)} data-testid="fnol-keep-location" />
          Keep photo location data (off by default — we remove it)</label>
        <PolicyCheck policyId={f.policy_id} date={f.incident_date} rego={f.rego} cls={f.claim_class} onVehicles={setVehicles} />
        {err ? <p className="brokaid-error" data-testid="fnol-error">{err}</p> : null}
        <div className="brokaid-fnol__actions">
          <Link to="/c/claims" className="brokaid-btn brokaid-btn--secondary" data-testid="fnol-cancel">Cancel</Link>
          <button type="submit" disabled={busy} className="brokaid-btn brokaid-btn--primary" data-testid="fnol-submit">{busy ? "Sending…" : "Send to my broker"}</button>
        </div>
        <p className="brokaid-fnol__mocked-note">Your broker reviews every claim. Nothing here decides whether you're covered.</p>
      </form>
    </section>
  );
}
