/** R0.7 — /o/inbox: read-only list of client threads. Replies arrive with Messages (M2). */
import { useEffect, useState } from "react";
import { fmtDateTime } from "../../lib/format";
import { Basis, PageHead, cell, getJson, muted } from "./stubKit";

interface Thread {
  thread_id: string; client: string; subject: string; last_message: string; last_at: string;
  messages: { value: number; data_basis: string }; status: string; promise: string;
}

export function Inbox() {
  const [rows, setRows] = useState<Thread[] | null>(null);
  useEffect(() => { void getJson("/api/o/inbox").then((r) => setRows(r.ok ? r.data.threads : [])); }, []);
  if (!rows) return <p data-testid="inbox-loading" style={muted}>Loading…</p>;
  return (
    <div data-testid="inbox-page" style={{ maxWidth: 1100 }}>
      <PageHead eyebrow="WORK · INBOX" title="Inbox">
        <p style={muted} data-testid="inbox-count">{rows.length} client threads. Read-only for now.<Basis b="registry" /></p>
      </PageHead>
      {!rows.length ? <p style={muted} data-testid="inbox-empty">No client messages yet.</p> : (
        <table style={{ width: "100%", fontSize: 13, borderCollapse: "collapse" }} data-testid="inbox-table">
          <thead><tr style={{ textAlign: "left", ...muted }}>
            {["Thread", "Client", "Last message", "Promise", "Status"].map((h) => <th key={h} style={{ padding: "6px 10px" }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {rows.map((t, i) => (
              <tr key={t.thread_id} data-testid={`inbox-row-${i}`}>
                <td style={cell}>{t.subject}<div style={muted}>{t.messages.value} message{t.messages.value === 1 ? "" : "s"}<Basis b={t.messages.data_basis} /></div></td>
                <td style={cell}>{t.client}</td>
                <td style={cell}>{t.last_message}<div style={muted}>{fmtDateTime(t.last_at)}</div></td>
                <td style={cell} data-testid={`inbox-promise-${i}`}>{t.promise}</td>
                <td style={cell} data-testid={`inbox-status-${i}`}>{t.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
