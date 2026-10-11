/**
 * Operator Program View — Managed / Tracked chips per policy.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { MockedBadge } from "@brokaid/mocks";
import { fmtAud, fmtDate } from "../lib/format";

const API = import.meta.env.VITE_API_BASE || "";

interface Policy {
  policy_id: string;
  client_id: string;
  status: "managed" | "tracked" | "pending" | "lapsed";
  product: string;
  insurer_display: string;
  inception: string;
  expiry: string;
  premium: { amount: number; currency: string; symbol: string };
  imported_from: string | null;
  mocked_insurer: boolean;
}

function fmtMoney(p: Policy) {
  return fmtAud(p.premium.amount);
}

export function OperatorPolicies() {
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      const res = await fetch(`${API}/api/o/policies`, {
        credentials: "include",
      });
      if (!res.ok) {
        setError(`${res.status} ${res.statusText}`);
        return;
      }
      setPolicies((await res.json()).policies);
    })();
  }, []);

  return (
    <main className="surface-ledger brokaid-shell" data-testid="operator-policies-root">
      <header className="brokaid-shell__header">
        <span className="brokaid-shell__crumb">/o/policies</span>
        <span className="brokaid-shell__crumb">Program View</span>
      </header>
      <h1 className="brokaid-shell__title">Policies</h1>

      {error && (
        <div className="brokaid-matters__error" data-testid="operator-policies-error">
          {error}
        </div>
      )}

      <table className="brokaid-matters__table" data-testid="operator-policies-table">
        <thead>
          <tr>
            <th>Status</th>
            <th>Product</th>
            <th>Insurer</th>
            <th>Premium</th>
            <th>Inception</th>
            <th>Expiry</th>
            <th>Source</th>
          </tr>
        </thead>
        <tbody>
          {policies.map((p, i) => (
            <tr key={p.policy_id} data-testid={`operator-policy-row-${i}`}>
              <td>
                <span
                  className={`brokaid-chip brokaid-chip--${p.status}`}
                  data-testid={`operator-policy-chip-${p.status}`}
                >
                  {p.status}
                </span>
              </td>
              <td>{p.product}</td>
              <td>
                {p.insurer_display}{" "}
                {p.mocked_insurer && <MockedBadge label="carrier" />}
              </td>
              <td className="brokaid-matters__mono">{fmtMoney(p)}</td>
              <td className="brokaid-matters__mono">
                {fmtDate(p.inception)}
              </td>
              <td className="brokaid-matters__mono">
                {fmtDate(p.expiry)}
              </td>
              <td>{p.imported_from ?? "in-house"}</td>
            </tr>
          ))}
          {policies.length === 0 && !error && (
            <tr>
              <td colSpan={7} className="brokaid-matters__empty">
                No policies yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>

      <Link to="/o" className="brokaid-shell__back" data-testid="link-back-operator">
        Back
      </Link>
    </main>
  );
}
