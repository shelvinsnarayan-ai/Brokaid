import { useLoad } from "./compliance/api";

/** R1.9 — the brokerage's go-live checklist (same items the platform admin sees). */
export function GoLive() {
  const { data, err } = useLoad<any>("o/governance/go-live");
  if (err) return <p className="imp-err" data-testid="golive-error">{err}</p>;
  if (!data) return null;
  return (
    <div data-testid="golive">
      <p style={{ opacity: 0.7, fontSize: 12, letterSpacing: 1 }}>GO-LIVE CHECKLIST · STATUS {String(data.status).toUpperCase()}</p>
      <ul>{data.checklist.map((i: any) => <li key={i.key} data-testid={`golive-${i.key}`}>{i.done ? "✓" : "○"} {i.label}</li>)}</ul>
      <p data-testid="golive-missing">{data.missing.length ? `${data.missing.length} item(s) still missing.` : "Everything on the list is done."}</p>
    </div>
  );
}
