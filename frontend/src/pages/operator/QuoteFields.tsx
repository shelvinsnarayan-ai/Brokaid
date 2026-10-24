/** R3.8 — repair-quote summary fields under each file labelled "Repair quote". Values the PDF suggested are marked for checking. */
import { useEffect, useState } from "react";
import type { Item } from "../client/attachUpload";
import { muted } from "./stubKit";

export interface Quote { repairer?: string; total?: string; quote_no?: string | null; quote_date?: string | null }
const KEYS: [keyof Quote, string, string][] = [["repairer", "Repairer name", "text"], ["total", "Total A$ incl GST", "text"], ["quote_no", "Quote number", "text"], ["quote_date", "Quote date", "date"]];

export function useQuotes(items: Item[]) {
  const [quotes, setQuotes] = useState<Record<string, Quote>>({});
  useEffect(() => {
    const fresh = items.filter((x) => x.file_id && x.suggested && Object.keys(x.suggested).length && !quotes[x.file_id]);
    if (fresh.length) setQuotes((q) => ({ ...q, ...Object.fromEntries(fresh.map((x) => [x.file_id!, { ...x.suggested }])) }));
  }, [items]);
  const live = items.filter((x) => x.file_id && x.label === "repair_quote" && x.status === "done");
  const payload = Object.fromEntries(live.filter((x) => quotes[x.file_id!]).map((x) => [x.file_id!, quotes[x.file_id!]]));
  const edit = (fid: string, k: keyof Quote, v: string) => setQuotes((q) => ({ ...q, [fid]: { ...q[fid], [k]: v } }));
  return { quotes, live, payload, edit, seed: (s: Record<string, Quote>) => setQuotes(s), reset: () => setQuotes({}) };
}

export function QuoteFields({ q }: { q: ReturnType<typeof useQuotes> }) {
  if (!q.live.length) return null;
  return <>{q.live.map((it) => {
    const v = q.quotes[it.file_id!] || {};
    const sug = it.suggested || {};
    return (
      <fieldset key={it.key} data-testid={`quote-fields-${it.file_id}`} style={{ border: "1px solid rgba(127,140,165,.35)", padding: "8px 10px", margin: 0 }}>
        <legend style={{ ...muted, padding: "0 4px" }}>Quote summary · {it.file.name}</legend>
        {Object.keys(sug).length ? <p style={{ ...muted, margin: "0 0 6px", color: "#E5A100" }} data-testid={`quote-suggested-note-${it.file_id}`}>Suggested from PDF — check before sending</p> : null}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 8 }}>
          {KEYS.map(([k, label, type]) => (
            <label key={k} style={{ ...muted, display: "flex", flexDirection: "column", gap: 2 }}>
              <span>{label}{k === "repairer" || k === "total" ? "" : " (optional)"}{sug[k] && sug[k] === v[k] ? " · suggested" : ""}</span>
              <input type={type} value={(v[k] as string) || ""} maxLength={k === "repairer" ? 120 : 40} inputMode={k === "total" ? "decimal" : undefined}
                onChange={(e) => q.edit(it.file_id!, k, e.target.value)} data-testid={`quote-${k}-${it.file_id}`} />
            </label>
          ))}
        </div>
        {!v.repairer?.trim() || !v.total?.trim() ? <p style={{ ...muted, margin: "6px 0 0" }} data-testid={`quote-hint-${it.file_id}`}>Add repairer and total to show a summary to the client</p> : null}
      </fieldset>
    );
  })}</>;
}

export const quoteLine = (x: Quote) => [x.repairer, x.total ? `${audIncl(x.total)}` : null, x.quote_no, x.quote_date].filter(Boolean).join(" · ");
export const audIncl = (t: string) => `A$${Number(t).toLocaleString("en-AU", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} incl GST`;
