/**
 * /o/clients — list all clients for the tenant.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { KEmpty } from "../../components/ledger/KEmpty";

const API = import.meta.env.VITE_API_BASE || "";

interface Client {
  client_id: string; display_name: string;
  industry: string; domicile: string;
  jurisdiction_code: "AU" | "SG"; policy_count: number;
}

export function Clients() {
  const [items, setItems] = useState<Client[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    (async () => {
      const r = await fetch(`${API}/api/o/clients`, { credentials: "include" });
      if (!r.ok) { setError(`${r.status}`); return; }
      setItems((await r.json()).clients);
    })();
  }, []);
  if (error) return <div className="k-empty" data-testid="clients-error">Error: {error}</div>;
  return (
    <section data-testid="clients-page">
      <p className="ledger-main__eyebrow">Clients</p>
      <h1 className="ledger-main__title">Book</h1>
      {items.length === 0 ? (
        <KEmpty title="No clients yet" testId="clients-empty" />
      ) : (
        <table className="k-table" data-testid="clients-table">
          <thead>
            <tr><th>Name</th><th>Industry</th><th>Domicile</th><th>Jurisdiction</th><th className="k-num-th">Policies</th></tr>
          </thead>
          <tbody>
            {items.map((c) => (
              <tr
                key={c.client_id}
                onClick={() => (window.location.href = `/o/clients/${c.client_id}`)}
                data-testid={`clients-row-${c.client_id}`}
              >
                <td><Link to={`/o/clients/${c.client_id}`} style={{ color: "var(--copper)" }}>{c.display_name}</Link></td>
                <td>{c.industry}</td>
                <td>{c.domicile}</td>
                <td>{c.jurisdiction_code}</td>
                <td className="k-num">{c.policy_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
