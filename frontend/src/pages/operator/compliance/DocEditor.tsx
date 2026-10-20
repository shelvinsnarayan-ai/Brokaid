import { useState } from "react";
import { day, get, post } from "./api";

export function DocEditor({ d, role, onDone, setErr }: any) {
  const [open, setOpen] = useState<"" | "edit" | "history">("");
  const [idx, setIdx] = useState(0);
  const [body, setBody] = useState("");
  const [vers, setVers] = useState<any[]>([]);
  const [diff, setDiff] = useState<any[] | null>(null);
  const secs: [string, string][] = d.sections || [];
  const pick = (i: number) => { setIdx(i); setBody(secs[i]?.[1] || ""); };
  const history = () => get<any>(`o/compliance/library/${d.doc_id}/versions`).then((r) => { setVers(r.versions); setOpen("history"); setDiff(null); }).catch((e) => setErr(e.message));
  const compare = (a: number, b: number) => get<any>(`o/compliance/library/${d.doc_id}/diff?a=${a}&b=${b}`).then((r) => setDiff(r.lines)).catch((e) => setErr(e.message));
  async function save() {
    try { await post(`o/compliance/library/${d.doc_id}/sections/${idx}`, { body }, "PUT"); setOpen(""); onDone(); } catch (e: any) { setErr(e.message); }
  }
  return (
    <div className="lib-editor">
      <div className="imp-row">
        {d.status !== "live" && secs.length ? <button className="k-btn" onClick={() => { pick(0); setOpen(open === "edit" ? "" : "edit"); }} data-testid={`library-edit-${d.kind}`}>Edit wording</button> : null}
        <button className="k-btn" onClick={() => (open === "history" ? setOpen("") : history())} data-testid={`library-history-${d.kind}`}>Versions</button>
      </div>
      {open === "edit" ? (
        <div className="lib-panel" data-testid={`library-editor-${d.kind}`}>
          <select value={idx} onChange={(e) => pick(Number(e.target.value))} data-testid={`library-section-${d.kind}`}>
            {secs.map(([h], i) => <option key={i} value={i}>{i + 1}. {h}</option>)}
          </select>
          <textarea rows={8} value={body} onChange={(e) => setBody(e.target.value)} data-testid={`library-section-body-${d.kind}`} />
          <p className="imp-muted">Saving makes a new draft version. Blanks you leave as [PLACEHOLDER: …] still count as unfilled.</p>
          <button className="k-btn k-btn--primary" disabled={body.trim().length < 10} onClick={save} data-testid={`library-section-save-${d.kind}`}>Save as new version</button>
        </div>
      ) : null}
      {open === "history" ? (
        <div className="lib-panel" data-testid={`library-versions-${d.kind}`}>
          <table className="imp-table"><tbody>{vers.map((v, i) => (
            <tr key={v.version}><td>v{v.version}</td><td>{v.reason}</td><td>{day(v.at)}</td>
              <td>{v.blanks ? `${v.blanks.filled} of ${v.blanks.total}` : ""}</td><td className="imp-mono">{v.sha256.slice(0, 12)}</td>
              <td>{i > 0 && role === "principal" ? <button className="k-btn" onClick={() => compare(vers[i - 1].version, v.version)} data-testid={`library-diff-${d.kind}-${v.version}`}>Diff vs v{vers[i - 1].version}</button> : null}</td></tr>
          ))}</tbody></table>
          {diff ? <pre className="lib-diff" data-testid={`library-diff-view-${d.kind}`}>{diff.filter((l) => l.op !== " ").map((l, i) => (
            <span key={i} className={l.op === "+" ? "lib-add" : "lib-del"}>{l.op} {l.line}{"\n"}</span>))}{diff.every((l) => l.op === " ") ? "No differences." : null}</pre> : null}
        </div>
      ) : null}
    </div>
  );
}
