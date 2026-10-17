import { useState } from "react";

/** R1.7 DDO: retail clients need a TMD-fit check before a recommendation; "No" needs a Principal. */
export function TmdCheck({ r, role, act }: { r: any; role?: string; act: (a: string, b?: unknown) => Promise<void> }) {
  const [f, setF] = useState({ fit: r.tmd_check?.fit || "yes", reason: "" });
  const t = r.tmd_check;
  return (
    <div className="rn-rec" data-testid="renewal-tmd">
      <h3 className="imp-h3">TMD-fit check (retail clients)</h3>
      {t ? <p className={t.fit === "no" && !t.principal_approved_by ? "imp-warn" : "imp-muted"} data-testid="renewal-tmd-status">
        In target market: <b>{t.fit}</b> — {t.reason}{t.fit === "no" ? (t.principal_approved_by ? " · approved by a Principal" : " · needs Principal approval") : ""}</p> : null}
      {role === "ae" || role === "principal" ? (
        <div className="imp-row">
          <select value={f.fit} onChange={(e) => setF({ ...f, fit: e.target.value })} data-testid="renewal-tmd-fit">
            <option value="yes">Yes, in the target market</option><option value="no">No</option><option value="unsure">Unsure</option></select>
          <input placeholder="Reason" value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} data-testid="renewal-tmd-reason" />
          <button className="k-btn" onClick={() => act("tmd-check", f)} data-testid="renewal-tmd-save">Record check</button>
        </div>
      ) : null}
      {t?.fit === "no" && !t.principal_approved_by && role === "principal" ? (
        <button className="k-btn k-btn--primary" onClick={() => act("approve-tmd")} data-testid="renewal-tmd-approve">Approve dealing outside the TMD</button>
      ) : null}
    </div>
  );
}
