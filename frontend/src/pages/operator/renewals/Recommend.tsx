import { useState } from "react";
import type { Detail } from "./api";

export function Recommend({ d, role, act }: { d: Detail; role?: string; act: (a: string, b?: unknown) => Promise<void> }) {
  const rec = d.renewal.recommendation;
  const [f, setF] = useState({ quote_id: rec?.quote_id || d.comparison.options[0]?.quote_id || "", text: rec?.text || "", reasons: (rec?.reasons || []).join("\n") });
  const blocked = rec?.tier === "P" && !rec.principal_approved_by;
  return (
    <div className="rn-rec" data-testid="renewal-recommendation">
      <h3 className="imp-h3">Recommendation</h3>
      {role === "ae" || role === "principal" ? (
        <>
          <label className="imp-row">Recommended option
            <select value={f.quote_id} onChange={(e) => setF({ ...f, quote_id: e.target.value })} data-testid="renewal-rec-option">
              {d.comparison.options.map((o) => <option key={o.quote_id} value={o.quote_id}>{o.insurer} — ${o.premium}</option>)}
            </select>
          </label>
          <textarea rows={3} value={f.text} onChange={(e) => setF({ ...f, text: e.target.value })} placeholder="Recommendation in plain words"
            data-testid="renewal-rec-text" />
          <textarea rows={2} value={f.reasons} onChange={(e) => setF({ ...f, reasons: e.target.value })} placeholder="Reasons, one per line"
            data-testid="renewal-rec-reasons" />
          <button className="k-btn" onClick={() => act("recommendation", { quote_id: f.quote_id, text: f.text, reasons: f.reasons.split("\n") })}
            data-testid="renewal-rec-save">Save recommendation</button>
        </>
      ) : null}
      {rec ? (
        <p data-testid="renewal-rec-tier" className={rec.tier === "P" ? "imp-warn" : "imp-muted"}>
          Advice boundary: tier {rec.tier} ({rec.rule_id}) — {rec.reason}.
          {rec.tier === "P" ? (rec.principal_approved_by ? " Approved by a Principal." : " A Principal must approve before the client sees it.") : ""}
        </p>
      ) : null}
      <p className="imp-muted rn-gaw" data-testid="renewal-gaw">{rec?.tier === "P" ? (rec as any).soa_note : d.general_advice_warning}</p>
      {rec && blocked && role === "principal" ? (
        <button className="k-btn k-btn--primary" onClick={() => act("approve-advice")} data-testid="renewal-approve-advice">Approve this advice</button>
      ) : null}
      {rec && d.renewal.current === "recommendation_ready" && (role === "ae" || role === "principal") ? (
        <button className="k-btn k-btn--primary" disabled={blocked} onClick={() => act("send")} data-testid="renewal-send">
          {blocked ? "Waiting for Principal approval" : "Send options to the client"}</button>
      ) : null}
    </div>
  );
}
