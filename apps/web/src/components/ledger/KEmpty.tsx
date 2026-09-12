export function KEmpty({ title, hint, testId }: { title: string; hint?: string; testId?: string }) {
  return (
    <div className="k-empty" data-testid={testId}>
      <div style={{ fontWeight: 500, color: "var(--bone)", marginBottom: 4 }}>{title}</div>
      {hint && <div>{hint}</div>}
    </div>
  );
}
