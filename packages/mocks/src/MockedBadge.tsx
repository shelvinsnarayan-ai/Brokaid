import "./MockedBadge.css";

/**
 * A pill that marks any surface calling a mocked external system.
 *
 * Ground rule 5: any such surface MUST render this badge, and the
 * corresponding ledger event MUST carry `mocked: true`.
 *
 * `variant`:
 *  - "operator" — Alert on Bone, for Ledger (dark) surfaces
 *  - "client"   — Coral on Sand, for Harbour (light) surfaces
 */
export type MockedLabel =
  | "carrier"
  | "payment"
  | "e-sign"
  | "insurer"
  | "ams"
  | "funder"
  | "signal";

export interface MockedBadgeProps {
  label: MockedLabel;
  variant?: "operator" | "client";
}

export function MockedBadge({ label, variant = "operator" }: MockedBadgeProps) {
  return (
    <span
      className={`brokaid-mocked brokaid-mocked--${variant}`}
      role="status"
      aria-label="This action is mocked for demo purposes"
      data-testid={`mocked-badge-${label}`}
    >
      MOCKED · {label.toUpperCase()}
    </span>
  );
}
