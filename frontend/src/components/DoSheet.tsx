/** R0.6 — Do sheet: six verbs in archetype order (Addendum 2 §5.5). */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

const API = import.meta.env.VITE_API_BASE || "";

export function DoSheet({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const [policyId, setPolicyId] = useState<string | null>(null);
  useEffect(() => {
    fetch(`${API}/api/c/policies`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : { policies: [] }))
      .then((b) => {
        const managed = (b.policies || []).filter((p: any) => p.status === "managed");
        const motor = managed.find((p: any) => String(p.product || "").startsWith("Motor"));
        setPolicyId((motor || managed[0])?.policy_id ?? null);
      })
      .catch(() => {});
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);
  const verbs = [
    { id: "certificate", label: "Get a certificate", to: policyId ? `/c/cover/${policyId}/certificate/new` : "/c/cover" },
    { id: "change", label: "Change something", to: policyId ? `/c/cover/${policyId}/request-change` : "/c/cover" },
    { id: "claim", label: "Report a claim", to: "/c/claims/new" },
    { id: "add", label: "Add or insure something", to: "/c/ask" },
    { id: "life", label: "Something's changed", to: "/c/ask" },
    { id: "ask", label: "Ask my broker", to: "/c/ask" },
  ];
  return (
    <div className="brokaid-sheet" role="dialog" aria-label="Do something" data-testid="do-sheet" onClick={onClose}>
      <div className="brokaid-sheet__panel" onClick={(e) => e.stopPropagation()}>
        <p className="brokaid-sheet__title">What do you need to do?</p>
        <ul className="brokaid-sheet__list">
          {verbs.map((v) => (
            <li key={v.id}>
              <button type="button" className="brokaid-sheet__verb" data-testid={`do-verb-${v.id}`}
                onClick={() => { onClose(); navigate(v.to); }}>
                {v.label}
              </button>
            </li>
          ))}
        </ul>
        <button type="button" className="brokaid-sheet__close" onClick={onClose} data-testid="do-sheet-close">Close</button>
      </div>
    </div>
  );
}
