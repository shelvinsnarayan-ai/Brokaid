import { useEffect, useState } from "react";
import { authCall } from "../../lib/authApi";

interface Assignment { assigned: { user_id: string; name: string } | null; aes: { user_id: string; name: string }[]; can_edit: boolean; warning: string | null }

export function AssignedAe({ clientId }: { clientId: string }) {
  const [a, setA] = useState<Assignment | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = () => authCall<Assignment>(`o/clients/${clientId}/assignment`, undefined, "GET").then(setA).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, [clientId]);
  if (!a) return err ? <p className="brokaid-form__err" data-testid="assigned-ae-error">{err}</p> : null;
  const save = async (userId: string) => {
    setErr(null);
    try { await authCall(`o/clients/${clientId}/assigned-ae`, { user_id: userId || null }, "PUT"); load(); }
    catch (e: any) { setErr(e.message); }
  };
  return (
    <div className="k-meta-cell" data-testid="assigned-ae" style={{ margin: "12px 0", display: "grid", gap: 6 }}>
      <div className="k-meta-cell__label">Account executive</div>
      {a.can_edit ? (
        <select value={a.assigned?.user_id || ""} onChange={(e) => save(e.target.value)} data-testid="assigned-ae-select"
          style={{ maxWidth: 280, minHeight: 32 }}>
          <option value="">Not assigned</option>
          {a.aes.map((x) => <option key={x.user_id} value={x.user_id}>{x.name}</option>)}
        </select>
      ) : <div className="k-meta-cell__value" data-testid="assigned-ae-name">{a.assigned?.name || "Not assigned"}</div>}
      {a.warning ? <p role="alert" data-testid="assigned-ae-warning" style={{ margin: 0, fontSize: 12, color: "var(--alert, #B42318)" }}>{a.warning}</p> : null}
      {err ? <p className="brokaid-form__err" data-testid="assigned-ae-error">{err}</p> : null}
    </div>
  );
}
