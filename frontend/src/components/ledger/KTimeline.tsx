import { fmtDateTime } from "../../lib/format";
interface TimelineItem {
  event_id: string;
  ts: string;
  type: string;
  actor?: { kind?: string; name?: string; id?: string };
  payload?: Record<string, any>;
  summary?: string;
}

/**
 * Per-event-type one-line renderers. Lookup table replaces the older
 * 7-branch if-chain (cyclomatic complexity 9 → 3). Missing types fall
 * through to the generic JSON-slice preview.
 */
const ONE_LINE_FORMATTERS: Record<string, (p: Record<string, any>) => string> = {
  Approval:                (p) => `${p.decision || "approve"} by ${p.role || p.user_id || "user"}`,
  AgentOutputProduced:     (p) => `Proposal for ${p.class_key ?? "class"}`,
  QueueCardEnqueued:       (p) => `${p.class_key} queued (${p.gate_reason ?? "hitl"})`,
  ComplianceBlockAdded:    (p) => `Block · ${p.reason ?? "no reason"}`,
  ComplianceBlockCleared:  (p) => `Block cleared · ${p.reason ?? ""}`,
  MatterCreated:           (p) => p.subject ?? "matter created",
  MatterStatusChanged:     (p) => `status → ${p.status}`,
};

function oneLine(item: TimelineItem): string {
  if (item.summary) return item.summary;
  const p = item.payload || {};
  const fmt = ONE_LINE_FORMATTERS[item.type];
  return fmt ? fmt(p) : JSON.stringify(p).slice(0, 80);
}

function shortTs(ts: string): string {
  return fmtDateTime(ts);
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
