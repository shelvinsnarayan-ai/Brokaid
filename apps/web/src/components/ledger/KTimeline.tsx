interface TimelineItem {
  event_id: string;
  ts: string;
  type: string;
  actor?: { kind?: string; name?: string; id?: string };
  payload?: Record<string, any>;
  summary?: string;
}

function oneLine(item: TimelineItem): string {
  if (item.summary) return item.summary;
  const p = item.payload || {};
  if (item.type === "Approval") return `${p.decision || "approve"} by ${p.role || p.user_id || "user"}`;
  if (item.type === "AgentOutputProduced") return `Proposal for ${p.class_key ?? "class"}`;
  if (item.type === "QueueCardEnqueued") return `${p.class_key} queued (${p.gate_reason ?? "hitl"})`;
  if (item.type === "ComplianceBlockAdded") return `Block · ${p.reason ?? "no reason"}`;
  if (item.type === "ComplianceBlockCleared") return `Block cleared · ${p.reason ?? ""}`;
  if (item.type === "MatterCreated") return p.subject ?? "matter created";
  if (item.type === "MatterStatusChanged") return `status → ${p.status}`;
  return JSON.stringify(p).slice(0, 80);
}

function shortTs(ts: string): string {
  return ts.replace("T", " ").slice(0, 19);
}

export function KTimeline({ items, testId }: { items: TimelineItem[]; testId?: string }) {
  if (items.length === 0) {
    return (
      <div className="k-empty" data-testid={testId ?? "k-timeline-empty"}>
        No events yet.
      </div>
    );
  }
  return (
    <ul className="k-timeline" data-testid={testId ?? "k-timeline"}>
      {items.map((it) => (
        <li key={it.event_id} className="k-timeline__item">
          <span className="k-timeline__ts">{shortTs(it.ts)}</span>
          <span className="k-timeline__type">{it.type}</span>
          <span>{oneLine(it)}</span>
        </li>
      ))}
    </ul>
  );
}
