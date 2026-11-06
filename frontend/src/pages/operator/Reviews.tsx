/** R3.11 (+R3.14 vs-previous columns): read-only multi-model review reports (Operator and Principal). Summary table plus PDF and raw JSON downloads. */
import { useEffect, useState } from "react";

const API = (import.meta as any).env.VITE_API_BASE || "";
const muted = { color: "var(--slate)", fontSize: 12 } as const;
const card = { background: "rgba(127,140,165,.06)", border: "1px solid rgba(127,140,165,.3)", padding: 16, marginBottom: 16 } as const;
type Row = { row: string; label: string; surface: string; scores: Record<string, number | null>; mean: number | null; baseline: number | null; pass: boolean | null; disagreement: boolean };
interface Review { review_id: string; title: string; created_at: string; sha: string | null; models: { name: string; model_id: string }[];
  consolidated: { rows: Row[]; surfaces: Record<string, { per_model: Record<string, number | null>; consensus: number | null; baseline: number; rows_passing: number; rows_scored: number }>;
    fixes: { label: string; surface: string; priority: number; frequency: number; gap: number }[] }; raw_models: string[];
  compare?: Compare | null }
type CmpS = { mean: number | null; prev: number | null; delta: number | null; mean_wo: number | null; prev_wo: number | null; delta_wo: number | null;
  rows_passing: number; rows_passing_wo: number; prev_rows_passing: number; prev_rows_passing_wo: number; rows_scored: number };
type Compare = { prev_review_id: string; surfaces: Record<string, CmpS>;
  rows: { row: string; label: string; surface: string; mean: number | null; prev: number | null; delta: number | null; mean_wo: number | null;
    prev_wo: number | null; delta_wo: number | null; pass: boolean | null; pass_wo: boolean | null }[] }

const f = (v: number | null | undefined) => (v == null ? "–" : v.toFixed(v % 1 ? 2 : 0));
const sd = (v: number | null | undefined) => (v == null ? "–" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}`);
const yn = (v: boolean | null | undefined) => (v == null ? "–" : v ? "yes" : "no");

function VsPrevious({ c }: { c: Compare }) {
  const p = c.prev_review_id.split("-")[0];
  const th = { padding: "2px 6px" } as const;
  return (
    <div style={{ overflowX: "auto", marginBottom: 12 }} data-testid="review-compare">
      <h3 style={{ fontSize: 14 }}>vs {p}</h3>
      <p style={muted}>Consensus = mean of the 4 models; "no Gemini" = mean of the other 3.</p>
      <table data-testid="review-compare-surfaces" style={{ fontSize: 12, borderCollapse: "collapse", marginBottom: 8 }}>
        <thead><tr>{["Surface", "Consensus", p, `vs ${p}`, "No Gemini", `${p} no Gemini`, `vs ${p} (no Gemini)`, "Rows ≥3.0", "Rows ≥3.0 (no Gemini)"].map((h) => <th key={h} style={th}>{h}</th>)}</tr></thead>
        <tbody>{Object.entries(c.surfaces).map(([s, m]) => (
          <tr key={s}><td>{s}</td><td style={{ fontWeight: 700 }} data-testid={`review-compare-${s}`}>{f(m.mean)}</td><td>{f(m.prev)}</td><td>{sd(m.delta)}</td>
            <td style={{ fontWeight: 700 }} data-testid={`review-compare-${s}-wo`}>{f(m.mean_wo)}</td><td>{f(m.prev_wo)}</td><td>{sd(m.delta_wo)}</td>
            <td>{m.rows_passing}/{m.rows_scored} (was {m.prev_rows_passing})</td><td>{m.rows_passing_wo}/{m.rows_scored} (was {m.prev_rows_passing_wo})</td></tr>))}</tbody>
      </table>
      <table data-testid="review-compare-rows" style={{ fontSize: 12, borderCollapse: "collapse", minWidth: 760 }}>
        <thead><tr>{["Row", "Surface", "Consensus", p, `vs ${p}`, "≥3.0", "No Gemini", `${p} no Gemini`, `vs ${p} (no Gemini)`, "≥3.0 (no Gemini)"].map((h) => <th key={h} style={th}>{h}</th>)}</tr></thead>
        <tbody>{c.rows.map((x) => (
          <tr key={x.row + x.surface} style={{ borderTop: "1px solid rgba(127,140,165,.2)" }}>
            <td>{x.label}</td><td>{x.surface}</td><td>{f(x.mean)}</td><td>{f(x.prev)}</td><td>{sd(x.delta)}</td><td>{yn(x.pass)}</td>
            <td>{f(x.mean_wo)}</td><td>{f(x.prev_wo)}</td><td>{sd(x.delta_wo)}</td><td>{yn(x.pass_wo)}</td></tr>))}</tbody>
      </table>
    </div>
  );
}

function Summary({ r }: { r: Review }) {
  const names = r.models.map((m) => m.name);
  const dl = (kind: string) => `${API}/api/o/reviews/${encodeURIComponent(r.review_id)}/download/${kind}`;
  return (
    <section style={card} data-testid={`review-${r.review_id}`}>
      <h2 style={{ fontSize: 16, margin: 0 }}>{r.title}</h2>
      <p style={muted}>{new Date(r.created_at).toLocaleString("en-AU")} · build {r.sha || "?"} · preview evidence (Harbourline demo)</p>
      <p style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <a className="k-btn k-btn--primary" href={dl("pdf")} data-testid="review-download-pdf">Download PDF</a>
        <a className="k-btn" href={dl("md")} data-testid="review-download-md">Markdown</a>
        {r.raw_models.map((m) => <a key={m} className="k-btn" href={dl(`raw-${m}`)} data-testid={`review-download-raw-${m}`}>Raw JSON · {m}</a>)}
      </p>
      <table data-testid="review-headline" style={{ fontSize: 12, borderCollapse: "collapse", marginBottom: 12 }}>
        <thead><tr><th style={{ textAlign: "left" }}>Surface</th>{names.map((n) => <th key={n}>{n}</th>)}<th>Consensus</th><th>Baseline</th><th>Rows ≥3.0</th></tr></thead>
        <tbody>{Object.entries(r.consolidated.surfaces).map(([s, m]) => (
          <tr key={s}><td>{s}</td>{names.map((n) => <td key={n} style={{ textAlign: "center" }}>{f(m.per_model[n])}</td>)}
            <td style={{ textAlign: "center", fontWeight: 700 }} data-testid={`review-consensus-${s}`}>{f(m.consensus)}</td><td style={{ textAlign: "center" }}>{m.baseline}</td>
            <td style={{ textAlign: "center" }}>{m.rows_passing}/{m.rows_scored}</td></tr>))}</tbody>
      </table>
      {r.compare ? <VsPrevious c={r.compare} /> : null}
      <div style={{ overflowX: "auto" }}>
        <table data-testid="review-matrix" style={{ fontSize: 12, borderCollapse: "collapse", minWidth: 640 }}>
          <thead><tr><th style={{ textAlign: "left" }}>Row</th><th>Surface</th>{names.map((n) => <th key={n}>{n}</th>)}<th>Mean</th><th>Baseline</th><th>Pass</th></tr></thead>
          <tbody>{r.consolidated.rows.map((x) => (
            <tr key={x.row + x.surface} style={{ borderTop: "1px solid rgba(127,140,165,.2)" }}>
              <td>{x.label}</td><td>{x.surface}</td>{names.map((n) => <td key={n} style={{ textAlign: "center" }}>{f(x.scores[n])}</td>)}
              <td style={{ textAlign: "center", fontWeight: 700 }}>{f(x.mean)}{x.disagreement ? " ⚠" : ""}</td><td style={{ textAlign: "center" }}>{f(x.baseline)}</td>
              <td style={{ textAlign: "center" }}>{x.pass == null ? "–" : x.pass ? "yes" : "no"}</td></tr>))}</tbody>
        </table>
      </div>
      <h3 style={{ fontSize: 14 }}>Top fixes</h3>
      <ol style={muted} data-testid="review-fixes">{r.consolidated.fixes.slice(0, 5).map((x) => <li key={x.label + x.surface}>{x.label} ({x.surface}) · priority {x.priority}</li>)}</ol>
      <p style={muted}>⚠ = models differ by 2 or more. Bar: 3.0 on every row, both surfaces.</p>
    </section>
  );
}

export default function Reviews() {
  const [list, setList] = useState<Review[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    (async () => {
      const r = await fetch(`${API}/api/o/reviews`, { credentials: "include" });
      if (!r.ok) { setErr(r.status === 403 ? "Reviews are for the Operator and Principal." : "Couldn't load reviews."); return; }
      const ids: { review_id: string }[] = (await r.json()).reviews;
      setList(await Promise.all(ids.map(async (x) => (await (await fetch(`${API}/api/o/reviews/${encodeURIComponent(x.review_id)}`, { credentials: "include" })).json()).review)));
    })();
  }, []);
  return (
    <div data-testid="reviews-page" style={{ padding: 16, maxWidth: 1100 }}>
      <h1 style={{ fontSize: 22 }}>Reviews</h1>
      {err ? <p role="alert" data-testid="reviews-error">{err}</p> : null}
      {list && !list.length ? <p style={muted} data-testid="reviews-empty">No reviews yet.</p> : null}
      {list?.map((r) => <Summary key={r.review_id} r={r} />)}
    </div>
  );
}
