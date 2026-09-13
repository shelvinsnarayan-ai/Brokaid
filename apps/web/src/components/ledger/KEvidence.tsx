interface EvidenceItem {
  event_id: string;
  ts: string;
  type: string;
  payload?: Record<string, any>;
}
export function KEvidence({ items, testId }: { items: EvidenceItem[]; testId?: string }) {
  if (!items.length) {
    return (
      <div className="k-empty" data-testid={testId ?? "k-evidence-empty"}>
        No cited evidence on this proposal.
      </div>
    );
  }
  return (
    <div data-testid={testId ?? "k-evidence"}>
      {items.map((it) => {
        const isCarrier = it.type === "CarrierQuoted";
        const isSubmission = it.type === "SubmissionDrafted";
        const carrier = isCarrier ? (it.payload as any) : null;
        return (
          <div key={it.event_id} className="k-evidence__item">
            <div className="k-evidence__meta">
              {it.type} · {it.ts.replace("T", " ").slice(0, 19)} · {it.event_id.slice(0, 8)}…
              {isCarrier && (
                <span
                  className="k-chip k-chip--warn"
                  style={{ marginLeft: 8 }}
                  data-testid="k-evidence-carrier-mocked"
                >
                  mocked · carrier
                </span>
              )}
            </div>
            <div className="k-evidence__body">
              {isCarrier && carrier ? (
                <>
                  <strong>{carrier.carrier_name}</strong> · new annual{" "}
                  {carrier.premium_currency} {carrier.premium_new_annual} ·
                  change {carrier.premium_change_amount}. Terms: {carrier.terms}
                </>
              ) : isSubmission ? (
                <>
                  <strong>Submission drafted.</strong>{" "}
                  {(it.payload as any)?.submission_summary || JSON.stringify(it.payload).slice(0, 200)}
                </>
              ) : (
                JSON.stringify(it.payload || {}, null, 0).slice(0, 260)
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
