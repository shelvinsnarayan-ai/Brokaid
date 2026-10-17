import { useEffect, useState } from "react";
import { post, useLoad } from "./api";

const FIELDS = [["gaw_text", "General advice warning (shown and recorded with every G-tier recommendation)"],
  ["afca_text", "AFCA details (added to every final complaint response)"], ["privacy_policy_text", "Privacy policy"],
  ["collection_notice_text", "Collection notice (shown and recorded at invite acceptance)"]] as const;

export function Settings({ role }: { role?: string }) {
  const { data, err, load, setErr } = useLoad<any>("o/compliance/settings");
  const [f, setF] = useState<Record<string, any>>({});
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => { if (data) setF({ ...data.settings, retention_years: data.retention_years }); }, [data]);
  const ro = role !== "principal" && role !== "operator";
  async function save() {
    setErr(null); setMsg(null);
    try { await post("o/compliance/settings", f, "PUT"); setMsg("Saved."); load(); } catch (e: any) { setErr(e.message); }
  }
  if (!data) return <p className="imp-muted">{err || "Loading…"}</p>;
  return (
    <div className="imp-card rn-rec" data-testid="compliance-settings">
      {FIELDS.map(([k, label]) => (
        <label key={k} className="rn-market">{label}
          <textarea rows={3} value={f[k] || ""} disabled={ro} onChange={(e) => setF({ ...f, [k]: e.target.value })} data-testid={`compliance-setting-${k}`} />
        </label>
      ))}
      <div className="imp-row">
        {Object.entries(f.retention_years || {}).map(([k, v]) => (
          <label key={k} className="imp-row">{k} (years)
            <input type="number" min={1} max={50} value={v as number} disabled={ro}
              onChange={(e) => setF({ ...f, retention_years: { ...f.retention_years, [k]: Number(e.target.value) } })} data-testid={`retention-years-${k}`} />
          </label>))}
      </div>
      {!ro ? <button className="k-btn k-btn--primary" onClick={save} data-testid="compliance-settings-save">Save</button> : null}
      {msg ? <p className="imp-muted" data-testid="compliance-settings-msg">{msg}</p> : null}
      {err ? <p className="imp-err" role="alert">{err}</p> : null}
    </div>
  );
}
