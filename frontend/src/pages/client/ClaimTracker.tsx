import { Link, useParams } from "react-router-dom";
import { useC } from "./api";
import { useUnreadStore } from "../../state/unreadStore";

const STEPS: [string, string[]][] = [
  ["Lodged", ["lodged"]], ["Broker reviewing", ["triage", "info_requested"]], ["Sent to insurer", ["submitted"]],
  ["Assessment", ["assessing"]], ["Decision", ["decision"]], ["Settled", ["settlement", "closed"]],
];

interface Claim { stage: string; claim_reference: string; status_text: string; info_requests?: { req_id: string; status: string; message?: string; due?: string }[] }

export function ClaimTracker() {
  const { claim_id } = useParams();
  const { data } = useC<{ claim: Claim }>(`c/claims/${claim_id}`);
  const home = useC<{ broker: { name: string; phone?: string | null } }>("c/home").data;
  const unread = useUnreadStore((st) => st.threads.find((x) => x.thread_id === `claim-${claim_id}`)?.unread || 0);
  if (!data) return null;
  const c = data.claim;
  const at = STEPS.findIndex(([, ks]) => ks.includes(c.stage));
  const open = (c.info_requests || []).filter((r) => r.status === "open");
  return (
    <div data-testid="mer-claim-tracker">
      <div className="mer-hero">
        <Link to="/c/activity?f=claims" style={{ color: "#fff", opacity: .8, fontSize: 13, textDecoration: "none" }} data-testid="mer-claim-back">‹ Activity</Link>
        <h1 className="mer-h" style={{ margin: "8px 0 0", fontSize: 20 }}>Claim {c.claim_reference}</h1>
        <p style={{ margin: "4px 0 0", fontSize: 13 }}>{c.status_text}</p>
      </div>
      {open.length ? (
        <div className="mer-need" data-testid="mer-claim-need">
          <p style={{ margin: 0, fontWeight: 700 }}>What we need from you</p>
          {open.map((r) => <p key={r.req_id} className="mer-row__s">{r.message || "Your broker needs more information."}{r.due ? ` · by ${r.due}` : ""}</p>)}
          <a href="#claim-upload" className="mer-btn" style={{ marginTop: 8 }} data-testid="mer-claim-upload-link">Upload now</a>
        </div>
      ) : null}
      <Link to={`/c/messages/claim/${claim_id}`} className="mer-btn" style={{ margin: "14px 16px 0", display: "flex", justifyContent: "center", gap: 8 }}
        data-testid="mer-claim-thread-btn">Message about this claim{unread ? <span className="mer-badge" style={{ margin: 0 }} data-testid="mer-claim-thread-unread">{unread}</span> : null}</Link>
      <section className="mer-sec">
        <h2 className="mer-sec__title">Progress</h2>
        <ol className="mer-card mer-steps" data-testid="mer-claim-steps">
          {STEPS.map(([label], i) => (
            <li key={label} className={`mer-step${i < at || c.stage === "closed" ? " mer-step--done" : i === at ? " mer-step--now" : ""}`}
              aria-current={i === at ? "step" : undefined} data-testid={`mer-claim-step-${i}`}>
              <span className="mer-step__dot">{i < at || c.stage === "closed" ? "✓" : ""}</span>
              <span style={{ paddingTop: 2, fontWeight: i === at ? 700 : 500 }}>{label}</span>
            </li>
          ))}
        </ol>
        {c.stage === "withdrawn" ? <p className="mer-row__s">This claim was withdrawn.</p> : null}
      </section>
      <section className="mer-sec" data-testid="mer-claim-handler">
        <h2 className="mer-sec__title">Handled by</h2>
        <div className="mer-card"><div className="mer-row">
          <span className="mer-row__main"><p className="mer-row__t">Account executive</p><p className="mer-row__s">{home?.broker.name || "Your broker"}</p></span>
          <Link to={`/c/messages/claim/${claim_id}`} className="mer-copy" data-testid="mer-claim-message">Message</Link>
          {home?.broker.phone ? <a href={`tel:${home.broker.phone}`} className="mer-copy" data-testid="mer-claim-call">Call</a> : null}
        </div></div>
      </section>
      <section className="mer-sec"><h2 className="mer-sec__title">Timeline, documents and uploads</h2></section>
    </div>
  );
}
