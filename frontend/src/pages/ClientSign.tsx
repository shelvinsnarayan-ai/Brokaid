/**
 * ClientSign — /c/cover/:policy_id/sign/:envelope_id
 * R2.0: click-to-accept the endorsement schedule (not a digital signature), then the certificate regenerates.
 */
import { useNavigate, useParams } from "react-router-dom";
import { AcceptPanel } from "../components/AcceptPanel";

export function ClientSign() {
  const { policy_id = "", envelope_id = "" } = useParams();
  const navigate = useNavigate();
  return (
    <section className="brokaid-tab" data-testid="client-sign-root">
      <header className="brokaid-tab__head">
        <p className="brokaid-tab__eyebrow">Accept the change</p>
        <h1 className="brokaid-tab__title">Review and accept the endorsement</h1>
        <p className="brokaid-tab__lede">Read the change summary, tick to accept and type your name. Your certificate updates straight after.</p>
      </header>
      <AcceptPanel kind="endorsement" refId={envelope_id} onDone={(r) => {
        if (r.verifier_token) window.sessionStorage.setItem(`vfy:${policy_id}`, r.verifier_token);
        setTimeout(() => navigate(`/c/cover/${policy_id}/certificate/${envelope_id}`), 900);
      }} />
    </section>
  );
}

export function ClientAcceptanceReceipt({ kind }: { kind: "endorsement" | "renewal" }) {
  const { ref_id = "" } = useParams();
  return (
    <section className="brokaid-tab" data-testid="client-acceptance-receipt">
      <header className="brokaid-tab__head">
        <p className="brokaid-tab__eyebrow">Receipt</p>
        <h1 className="brokaid-tab__title">Your acceptance</h1>
      </header>
      <AcceptPanel kind={kind} refId={ref_id} />
    </section>
  );
}
