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
      {items.map((it) => (
        <div key={it.event_id} className="k-evidence__item">
          <div className="k-evidence__meta">
            {it.type} · {it.ts.replace("T", " ").slice(0, 19)} · {it.event_id.slice(0, 8)}…
          </div>
          <div className="k-evidence__body">
            {JSON.stringify(it.payload || {}, null, 0).slice(0, 260)}
          </div>
        </div>
      ))}
    </div>
  );
}
