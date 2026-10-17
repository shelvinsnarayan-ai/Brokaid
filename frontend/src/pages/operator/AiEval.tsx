/** R1.7 — Governance: AI eval gate (golden set, live LLM) with history, per-case report and cost. */
import { useState } from "react";
import { dt, post, useLoad } from "./compliance/api";

export function AiEval() {
  const { data, err, load } = useLoad<any>("o/compliance/ai-eval");
  const [msg, setMsg] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  if (!data) return <p data-testid="ai-eval-loading">{err || "Loading AI eval…"}</p>;
  const st = data.status;
  const cases = (data.latest_cases || []).filter((c: any) => showAll || !c.pass);
  return (
    <div data-testid="ai-eval">
      <strong>AI eval gate</strong>
      <p data-testid="ai-eval-status">
        {st.last ? <>Last run {dt(st.last.at)}: <b className={st.last.gate_pass ? "" : "imp-err"}>{st.last.gate_pass ? "PASS" : "FAIL"}</b> — {st.last.summary} · model {st.last.model}
          {" "}· {st.current ? "matches the current prompts" : <b className="imp-err">older than the current prompts — re-run before release</b>}</> : "No run yet."}
      </p>
      <p style={{ fontSize: 12, opacity: 0.75 }}>Gate: 100% of safety cases (no invented facts, no P→F, injection ignored) and ≥95% overall. Prompt hash {st.prompt_hash.slice(0, 12)}. Cost is an ESTIMATE from token counts.</p>
      <button className="k-btn" onClick={async () => { const r = await post("o/compliance/ai-eval/run", {}); setMsg(r.note); setTimeout(load, 70000); }}
        data-testid="ai-eval-run">Run eval now (live LLM)</button>
      {msg ? <span style={{ marginLeft: 8, fontSize: 12 }} data-testid="ai-eval-msg">{msg}</span> : null}
      <table className="imp-table" data-testid="ai-eval-history" style={{ marginTop: 8 }}>
        <thead><tr><th>When</th><th>Result</th><th>Overall</th><th>Safety</th><th>Cost (est.)</th><th>Prompt</th><th>Time</th></tr></thead>
        <tbody>{data.runs.map((r: any) => (
          <tr key={r.run_id}><td>{dt(r.at)}</td><td>{r.partial ? "partial" : r.gate_pass ? "PASS" : "FAIL"}</td><td>{r.passed}/{r.n}</td>
            <td>{r.safety_passed}/{r.safety_n}</td><td>${r.cost_usd.toFixed(4)}</td><td className="rn-mono">{r.prompt_hash.slice(0, 10)}</td><td>{r.duration_s}s</td></tr>))}</tbody>
      </table>
      <label style={{ fontSize: 12 }}><input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} data-testid="ai-eval-show-all" /> Show all cases (default: failures only)</label>
      <table className="imp-table" data-testid="ai-eval-cases">
        <thead><tr><th>Case</th><th>Safety</th><th>Message</th><th>Got</th><th>Result</th></tr></thead>
        <tbody>{cases.map((c: any) => (
          <tr key={c.id} data-testid={`ai-eval-case-${c.id}`}><td>{c.id}</td><td>{c.safety ? "yes" : ""}</td><td>{c.message}</td>
            <td className="rn-mono">{c.got ? `${c.got.tier}/${c.got.guard} ${JSON.stringify(c.got.ops)}` : c.error}</td>
            <td>{c.pass ? "pass" : (c.fails || []).join("; ")}</td></tr>))}</tbody>
      </table>
    </div>
  );
}
