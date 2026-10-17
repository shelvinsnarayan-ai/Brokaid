/** R1.8 — claim stage actions for the AE: acknowledge, request info, pack, submit, assessing, decision, settlement, close. */
import { useState } from "react";
import { API, post } from "../compliance/api";

function Act({ label, run, id, disabled }: { label: string; run: () => Promise<unknown>; id: string; disabled?: boolean }) {
  return <button className="k-btn" disabled={disabled} onClick={() => void run()} data-testid={id}>{label}</button>;
}

export function ClaimActions({ c, quotingMode, onDone }: { c: any; quotingMode: string; onDone: () => void }) {
  const [err, setErr] = useState<string | null>(null);
  const [f, setF] = useState<Record<string, string>>({ due: "", items: "", claim_no: "", lodged_on: new Date().toISOString().slice(0, 10), contact: "",
    assessor: "", repairer: "", appt: "", outcome: "accepted", reason: "", amount: "", s_amount: "", excess: String(c.excess ?? 0),
    payee: "client", payee_name: "", method: "trust", txn_id: "", why: "" });
  const set = (k: string, v: string) => setF((x) => ({ ...x, [k]: v }));
  const go = (path: string, body: unknown = {}) => post(`o/claims/${c.claim_id}/${path}`, body).then(() => { setErr(null); onDone(); }).catch((e) => setErr(e.message));
  const st = c.stage;
  const letters = c.files.filter((d: any) => d.kind === "decision_letter");
  return (
    <div className="imp-card" data-testid="claim-actions">
      <h2>Next steps</h2>
      {st === "lodged" ? <Act label="Acknowledge and start triage" id="claim-ack" run={() => go("ack")} /> : null}
      {["triage", "info_requested"].includes(st) ? <>
        <div className="clm-o-filters">
          <input placeholder="What you need (comma separated)" value={f.items} onChange={(e) => set("items", e.target.value)} data-testid="claim-info-items" />
          <input type="date" value={f.due} onChange={(e) => set("due", e.target.value)} data-testid="claim-info-due" />
          <Act label="Request info from client" id="claim-info-request" disabled={!f.items || !f.due}
            run={() => go("info-request", { items: f.items.split(",").map((s) => s.trim()).filter(Boolean), due: f.due })} />
        </div>
        <div className="clm-o-filters">
          <Act label={c.pack ? "Regenerate claim pack" : "Generate claim pack"} id="claim-pack" run={() => go("pack")} />
          {c.pack ? <><a href={`${API}/api/o/claims/${c.claim_id}/pack.pdf`} data-testid="claim-pack-pdf">Pack PDF</a>
            <a href={`${API}/api/o/claims/${c.claim_id}/pack.zip`} data-testid="claim-pack-zip">Attachments zip</a></> : null}
        </div>
        <div className="clm-o-filters">
          <input placeholder="Insurer claim no." value={f.claim_no} onChange={(e) => set("claim_no", e.target.value)} data-testid="claim-sub-no" />
          <input type="date" value={f.lodged_on} onChange={(e) => set("lodged_on", e.target.value)} data-testid="claim-sub-date" />
          <input placeholder="Insurer contact" value={f.contact} onChange={(e) => set("contact", e.target.value)} data-testid="claim-sub-contact" />
          <Act label="Record insurer submission" id="claim-sub-record" disabled={!f.claim_no}
            run={() => go("submission", { insurer_claim_no: f.claim_no, lodged_on: f.lodged_on, contact: f.contact })} />
          {quotingMode === "mocked_allowed" ? <Act label="Send via MOCKED insurer channel" id="claim-sub-mocked" run={() => go("submit-mocked")} /> : null}
        </div>
      </> : null}
      {["submitted", "assessing"].includes(st) ? <div className="clm-o-filters">
        <input placeholder="Assessor" value={f.assessor} onChange={(e) => set("assessor", e.target.value)} data-testid="claim-assessor" />
        <input placeholder="Repairer" value={f.repairer} onChange={(e) => set("repairer", e.target.value)} data-testid="claim-repairer" />
        <input placeholder="Appointment (e.g. 2026-11-02 10:00 inspection)" value={f.appt} onChange={(e) => set("appt", e.target.value)} data-testid="claim-appt" />
        <Act label={st === "assessing" ? "Update assessment" : "Mark assessing"} id="claim-assessing"
          run={() => go("assessing", { assessor: f.assessor, repairer: f.repairer, appointments: f.appt ? [{ when: f.appt, what: "" }] : [] })} />
      </div> : null}
      {["submitted", "assessing"].includes(st) ? <div className="clm-o-filters">
        <select value={f.outcome} onChange={(e) => set("outcome", e.target.value)} data-testid="claim-outcome">
          <option value="accepted">Accepted</option><option value="partial">Partially accepted</option><option value="declined">Declined</option>
        </select>
        <select value={f.letter || ""} onChange={(e) => set("letter", e.target.value)} data-testid="claim-letter">
          <option value="">Decision letter… (upload as decision_letter below)</option>
          {letters.map((d: any) => <option key={d.file_id} value={d.file_id}>{d.filename}</option>)}
        </select>
        <input placeholder="Amount accepted" value={f.amount} onChange={(e) => set("amount", e.target.value)} data-testid="claim-decision-amount" />
        <input placeholder="Insurer's reasons" value={f.reason} onChange={(e) => set("reason", e.target.value)} data-testid="claim-decision-reason" />
        <Act label="Record decision" id="claim-decision" disabled={!f.letter}
          run={() => go("decision", { outcome: f.outcome, letter_file_id: f.letter, reason: f.reason, amount: f.amount || null })} />
      </div> : null}
      {st === "decision" && c.decision?.outcome !== "declined" ? <div className="clm-o-filters">
        <input placeholder="Settlement amount" value={f.s_amount} onChange={(e) => set("s_amount", e.target.value)} data-testid="claim-settle-amount" />
        <input placeholder="Excess applied" value={f.excess} onChange={(e) => set("excess", e.target.value)} data-testid="claim-settle-excess" />
        <select value={f.payee} onChange={(e) => set("payee", e.target.value)} data-testid="claim-settle-payee">
          <option value="client">Client</option><option value="repairer">Repairer</option><option value="third_party">Third party</option>
        </select>
        <input placeholder="Payee name" value={f.payee_name} onChange={(e) => set("payee_name", e.target.value)} data-testid="claim-settle-payee-name" />
        <select value={f.method} onChange={(e) => set("method", e.target.value)} data-testid="claim-settle-method">
          <option value="trust">Paid via trust account</option><option value="insurer_direct">Paid directly by insurer</option>
        </select>
        {f.method === "trust" ? <input placeholder="Outgoing bank line id (optional)" value={f.txn_id} onChange={(e) => set("txn_id", e.target.value)} data-testid="claim-settle-txn" /> : null}
        <Act label="Record settlement" id="claim-settle" disabled={!f.s_amount}
          run={() => go("settle", { amount: f.s_amount, excess_applied: f.excess, payee: f.payee, payee_name: f.payee_name, method: f.method, txn_id: f.txn_id || null })} />
      </div> : null}
      {(st === "settlement" || (st === "decision" && c.decision?.outcome === "declined")) ? <Act label="Close claim" id="claim-close" run={() => go("close", { reason: "" })} /> : null}
      {c.decision?.disputed ? <p className="clm-o-flag" data-testid="claim-disputed">Client disputes the decision — consider logging an IDR complaint in <a href="/o/compliance">Compliance</a>.</p> : null}
      {!["closed", "withdrawn"].includes(st) ? <div className="clm-o-filters">
        <input placeholder="Reason to withdraw" value={f.why} onChange={(e) => set("why", e.target.value)} data-testid="claim-withdraw-reason" />
        <Act label="Withdraw" id="claim-withdraw" disabled={!f.why} run={() => go("withdraw", { reason: f.why })} />
      </div> : null}
      {err ? <p className="imp-err" role="alert" data-testid="claim-action-error">{err}</p> : null}
    </div>
  );
}
