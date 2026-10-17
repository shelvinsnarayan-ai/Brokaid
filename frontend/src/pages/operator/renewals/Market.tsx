import { useEffect, useState } from "react";
import { get, type Quote } from "./api";

export function Market({ id, quotes, act, mode }: { id: string; quotes: Quote[]; act: (a: string, b?: unknown) => Promise<void>; mode?: string }) {
  const [list, setList] = useState<string[]>([]);
  const [pick, setPick] = useState<string[]>([]);
  const [m, setM] = useState({ insurer: "", premium: "", excess: "", terms: "" });
  useEffect(() => { get("o/renewals/insurers").then((d) => setList(d.insurers)).catch(() => {}); }, [id]);
  const mocked = quotes.filter((q) => q.mocked).length;
  const toggle = (n: string) => setPick(pick.includes(n) ? pick.filter((x) => x !== n) : [...pick, n].slice(0, 3));
  return (
    <div className="rn-market" data-testid="renewal-market">
      <h3 className="imp-h3">Go to market</h3>
      {mode === "manual_only" ? <p className="imp-muted" data-testid="renewal-manual-only">This brokerage uses real insurer quotes only — mocked quoting is off.</p>
        : mocked < 3 ? (
        <>
          <p className="imp-muted">Pick up to {3 - mocked} insurer(s). Prices come from the MOCKED carrier channel (expiring premium ±15%).</p>
          <div className="imp-tabs">
            {list.map((n) => (
              <label key={n} className="rn-chip"><input type="checkbox" checked={pick.includes(n)} onChange={() => toggle(n)}
                data-testid={`renewal-insurer-${n.replace(/\W+/g, "-").toLowerCase()}`} /> {n}</label>
            ))}
          </div>
          <button className="k-btn k-btn--primary" disabled={!pick.length} onClick={() => act("request-quotes", { insurers: pick }).then(() => setPick([]))}
            data-testid="renewal-request-quotes">Request MOCKED quotes</button>
        </>
      ) : <p className="imp-muted">Mocked quotes requested from 3 insurers (the most allowed).</p>}
      <h3 className="imp-h3">Enter a real quote received offline</h3>
      <div className="imp-row">
        <input placeholder="Insurer" value={m.insurer} onChange={(e) => setM({ ...m, insurer: e.target.value })} data-testid="renewal-manual-insurer" />
        <input placeholder="Premium A$" value={m.premium} onChange={(e) => setM({ ...m, premium: e.target.value })} data-testid="renewal-manual-premium" />
        <input placeholder="Excess A$" value={m.excess} onChange={(e) => setM({ ...m, excess: e.target.value })} data-testid="renewal-manual-excess" />
        <input placeholder="Key terms (separate with ;)" value={m.terms} onChange={(e) => setM({ ...m, terms: e.target.value })} data-testid="renewal-manual-terms" />
        <button className="k-btn" onClick={() => act("manual-quote", { insurer: m.insurer, premium: m.premium, excess: m.excess,
          key_terms: m.terms.split(";").map((t) => t.trim()).filter(Boolean) }).then(() => setM({ insurer: "", premium: "", excess: "", terms: "" }))}
          data-testid="renewal-manual-add">Add quote</button>
      </div>
    </div>
  );
}
