/** R3.13 — staff help copy (one file) and the "?" / "How this works" components. */
import { useEffect, useRef, useState } from "react";

export const STAFF_HELP: Record<string, string> = {
  class: "What kind of request this is (for example a change of cover or a certificate). It decides which checks and wording apply.",
  confidence: "How sure the assistant is that its draft is right, based on the evidence it cited. Low confidence means read it closely; it never replaces your review.",
  route: "Who needs to act: an account executive, a Principal (personal advice or anything over limits) or Compliance.",
  tier: "G = general advice: facts and options, no recommendation for this client. P = personal advice: a recommendation that considers the client's situation, which needs a Principal's approval and a Statement of Advice.",
  reply_by: "When we've promised the client a reply: one business day after their message, skipping weekends and public holidays in the brokerage's state.",
  crn: "Customer reference number: the payment reference the client uses for BPAY or bank transfer, so the payment matches this invoice automatically.",
  signed_offline: "Use when the insurer needs a wet signature or the client signed on paper. Upload or record what was signed; it's kept in the ledger like an in-app acceptance.",
  lapse: "The policy ends on its expiry date without renewing. Confirm only after the client says no, or the renewal can't be bound in time; the client is told in plain words.",
  refund_or_bind: "The client paid but the renewal can't go ahead as quoted. Either bind on the agreed terms, or refund the money from the trust account. A Principal decides if terms changed.",
};

export function StaffHelp({ k, testid }: { k: keyof typeof STAFF_HELP | string; testid?: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", close); };
  }, [open]);
  const text = STAFF_HELP[k];
  if (!text) return null;
  return (
    <span className="staff-help" ref={ref} onClick={(e) => e.stopPropagation()}>
      <button type="button" className="staff-help__btn" aria-label={`What does this mean?`} aria-expanded={open} title={text}
        onClick={() => setOpen(!open)} data-testid={testid || `staff-help-${k}`}>?</button>
      {open ? <span className="staff-help__pop" role="tooltip" data-testid={`${testid || `staff-help-${k}`}-text`}>{text}</span> : null}
    </span>
  );
}

export const PLAYBOOKS: Record<string, { title: string; steps: string[] }> = {
  endorsement: { title: "How this works: approving a change", steps: [
    "Read the client's request and the assistant's draft side by side.",
    "Check the cited evidence (policy schedule, insurer quote). No evidence means don't approve from here.",
    "Edit the wording if anything's off; personal advice routes to a Principal.",
    "Approve: the client gets the quote to accept and pay in the app.",
    "Once they accept and pay, issue the change to the insurer and the updated schedule goes to Documents."] },
  claims: { title: "How this works: handling a claim", steps: [
    "Acknowledge within 1 business day; the client sees the reply-by date.",
    "Work the checklist: ask the client for anything missing in one message.",
    "Generate the claim pack and send it to the insurer; record the claim number they give you.",
    "Record assessment updates and the insurer's decision as they happen.",
    "Settle or close; the client can dispute a decision and you'll see it here."] },
  renewal: { title: "How this works: a renewal", steps: [
    "We start the renewal ahead of expiry and ask the client to confirm details and sums insured.",
    "Review their answers and any change requests, then quote with the insurer.",
    "Write the recommendation (G or P). P needs a Principal's approval.",
    "The client chooses an option, pays using the CRN and accepts.",
    "Bind with the insurer. If they paid but it can't bind as quoted, decide refund or bind."] },
};

export function Playbook({ k }: { k: keyof typeof PLAYBOOKS }) {
  const p = PLAYBOOKS[k];
  return (
    <details className="staff-playbook" data-testid={`playbook-${k}`}>
      <summary>{p.title}</summary>
      <ol>{p.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
    </details>
  );
}
