import { useEffect, useState } from "react";
import { api, ENTITY_LABEL, Entity, Fields, label, Run, Sheet, Upload, uploadFile } from "./api";

function MappingTable({ sheet, fields, onChange }: { sheet: Sheet; fields: Fields; onChange: (s: Sheet) => void }) {
  const tid = (x: string) => `import-map-${sheet.name}-${x}`.replace(/\s+/g, "-").toLowerCase();
  return (
    <div className="imp-sheet" data-testid={tid("card")}>
      <div className="imp-sheet__head">
        <strong>{sheet.name}</strong> <span className="imp-muted">{sheet.row_count} rows</span>
        <label> holds{" "}
          <select value={sheet.entity} data-testid={tid("entity")}
                  onChange={(e) => { const ent = e.target.value as Entity; onChange({ ...sheet, entity: ent, mapping: sheet.suggested[ent] }); }}>
            {Object.entries(ENTITY_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
      </div>
      <table className="imp-table">
        <thead><tr><th>Our field</th><th>Column in your file</th><th>Example</th></tr></thead>
        <tbody>
          {fields[sheet.entity].map(({ field, required }) => {
            const h = sheet.mapping[field] || "";
            const ix = sheet.headers.indexOf(h);
            return (
              <tr key={field} className={required && !h ? "imp-missing" : ""}>
                <td>{label(field)}{required ? <span className="imp-req" title="required"> *</span> : null}</td>
                <td>
                  <select value={h} data-testid={tid(`field-${field}`)}
                          onChange={(e) => onChange({ ...sheet, mapping: { ...sheet.mapping, [field]: e.target.value || null } })}>
                    <option value="">— not in this file —</option>
                    {sheet.headers.filter(Boolean).map((x) => <option key={x} value={x}>{x}</option>)}
                  </select>
                </td>
                <td className="imp-muted">{ix >= 0 ? sheet.sample[0]?.[ix] ?? "" : ""}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function UploadStep({ onRun }: { onRun: (r: Run) => void }) {
  const [fields, setFields] = useState<Fields | null>(null);
  const [up, setUp] = useState<Upload | null>(null);
  const [sheets, setSheets] = useState<Sheet[]>([]);
  const [skip, setSkip] = useState<Record<string, boolean>>({});
  const [profile, setProfile] = useState("iAdviser default");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api<{ entities: Fields }>("o/import/fields", undefined, "GET").then((d) => setFields(d.entities)).catch(() => {}); }, []);

  async function pick(f: File | undefined) {
    if (!f) return;
    setErr(null); setBusy(true); setUp(null);
    try { const u = await uploadFile(f); setUp(u); setSheets(u.sheets); setSkip({}); if (u.profile) setProfile(u.profile.name); }
    catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }
  async function check() {
    if (!up) return;
    setErr(null); setBusy(true);
    try {
      const run = await api<Run>("o/import/runs", {
        file_id: up.file_id, save_profile: profile.trim() || null,
        sheets: sheets.filter((s) => !skip[s.name]).map((s) => ({ name: s.name, entity: s.entity, mapping: s.mapping })),
      });
      onRun(run);
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }
  return (
    <section className="imp-card" data-testid="import-upload-step">
      <h2 className="imp-h2">1 · Upload an iAdviser export</h2>
      <p className="imp-muted">CSV or Excel (.xlsx), up to 20 MB. One data type per file, or one per sheet. Nothing is written until you confirm.</p>
      <label className="imp-drop" data-testid="import-file-label">
        <input type="file" accept=".csv,.xlsx" data-testid="import-file-input" onChange={(e) => pick(e.target.files?.[0])} />
        <span>{busy && !up ? "Reading file…" : up ? `${up.filename} · ${(up.size / 1024).toFixed(0)} KB` : "Choose a file"}</span>
      </label>
      {err ? <p className="brokaid-form__err" role="alert" data-testid="import-upload-error">{err}</p> : null}
      {up && fields ? (
        <>
          <h2 className="imp-h2">2 · Match columns</h2>
          {up.profile ? <p className="imp-muted" data-testid="import-profile-applied">Using saved profile “{up.profile.name}”.</p> : null}
          {sheets.map((s, i) => (
            <div key={s.name}>
              <label className="imp-muted"><input type="checkbox" checked={!skip[s.name]} data-testid={`import-include-${i}`}
                       onChange={(e) => setSkip({ ...skip, [s.name]: !e.target.checked })} /> Import this sheet</label>
              {!skip[s.name] && <MappingTable sheet={s} fields={fields} onChange={(n) => setSheets(sheets.map((x) => (x.name === s.name ? n : x)))} />}
            </div>
          ))}
          <div className="imp-row">
            <label>Save these matches as profile{" "}
              <input value={profile} onChange={(e) => setProfile(e.target.value)} data-testid="import-profile-name" />
            </label>
            <button className="k-btn k-btn--primary" disabled={busy} onClick={check} data-testid="import-check-button">
              {busy ? "Checking…" : "Check the file"}
            </button>
          </div>
        </>
      ) : null}
    </section>
  );
}
