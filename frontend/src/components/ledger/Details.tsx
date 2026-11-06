/** R3.13 — raw ids/payload/hashes live behind "Details": Operator and platform admin only, collapsed by default. */
import { useOperatorStore } from "../../state/operatorStore";

export const RAW_ROLES = new Set(["operator", "platform_admin"]);

export function useCanSeeRaw(): boolean {
  const role = useOperatorStore((s) => s.session?.session.active_role) || "";
  return RAW_ROLES.has(role);
}

export function Details({ value, testid }: { value: unknown; testid?: string }) {
  const ok = useCanSeeRaw();
  if (!ok || value == null) return null;
  return (
    <details className="k-details" data-testid={testid || "k-details"}>
      <summary>Details</summary>
      <pre>{typeof value === "string" ? value : JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}
