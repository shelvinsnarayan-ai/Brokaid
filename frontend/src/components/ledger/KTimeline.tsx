import { useState } from "react";
import { Link } from "react-router-dom";
import { fmtDateTime } from "../../lib/format";
import { Details } from "./Details";

interface TimelineItem {
  event_id: string;
  ts: string;
  type: string;
  actor?: { kind?: string; name?: string; id?: string };
  payload?: Record<string, any>;
  summary?: string;
  text?: string;
  link?: string | null;
}

/** R3.13: each event is a plain sentence from the server (event_text.py); raw type/payload sit behind Details. */
export function KTimeline({ items, testId, initial = 5 }: { items: TimelineItem[]; testId?: string; initial?: number }) {
  const [all, setAll] = useState(false);
  if (items.length === 0) {
    return <div className="k-empty" data-testid={testId ?? "k-timeline-empty"}>No events yet.</div>;
  }
  const shown = all ? items : items.slice(0, initial);
  return (
    <>
      <ul className="k-timeline" data-testid={testId ?? "k-timeline"}>
        {shown.map((it, i) => {
          const text = it.text || it.summary || "System update recorded";
          return (
            <li key={it.event_id || i} className="k-timeline__item" data-testid={`${testId ?? "k-timeline"}-item-${i}`}>
              <span>{it.link ? <Link to={it.link}>{text}</Link> : text} <span className="k-timeline__ts">· {fmtDateTime(it.ts)}</span></span>
              <Details value={{ type: it.type, event_id: it.event_id, actor: it.actor, payload: it.payload }} testid={`${testId ?? "k-timeline"}-details-${i}`} />
            </li>
          );
        })}
      </ul>
      {items.length > initial ? (
        <button type="button" className="k-btn" style={{ marginTop: 8 }} onClick={() => setAll(!all)} data-testid={`${testId ?? "k-timeline"}-show-all`}>
          {all ? "Show fewer" : `Show all ${items.length}`}
        </button>
      ) : null}
    </>
  );
}
