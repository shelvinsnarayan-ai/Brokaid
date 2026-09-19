interface Props {
  variant?: "info" | "overdue" | "warn" | "ok" | "role";
  children: React.ReactNode;
  testId?: string;
  title?: string;
}
export function KChip({ variant = "info", children, testId, title }: Props) {
  return (
    <span className={`k-chip k-chip--${variant}`} data-testid={testId} title={title}>
      {children}
    </span>
  );
}
