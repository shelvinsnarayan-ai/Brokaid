/**
 * EmptyState — every empty section in the client-app funnels through
 * this so the visual language stays consistent.
 */
export function EmptyState({
  title, body, cta, testId = "empty-state",
}: {
  title: string;
  body: string;
  cta?: React.ReactNode;
  testId?: string;
}) {
  return (
    <div className="brokaid-empty" data-testid={testId}>
      <h3 className="brokaid-empty__title">{title}</h3>
      <p className="brokaid-empty__body">{body}</p>
      {cta && <div className="brokaid-empty__cta">{cta}</div>}
    </div>
  );
}
