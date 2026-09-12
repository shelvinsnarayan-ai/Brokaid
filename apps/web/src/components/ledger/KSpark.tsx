/**
 * KSpark — 12-cell mini sparkline. Deterministic; no libraries.
 */
interface Props { series: number[]; testId?: string }

export function KSpark({ series, testId }: Props) {
  const cells = series.slice(-12);
  if (cells.length === 0) {
    return <div className="k-spark" data-testid={testId}></div>;
  }
  const min = Math.min(...cells);
  const max = Math.max(...cells);
  const span = max - min || 1;
  return (
    <div className="k-spark" data-testid={testId}>
      {cells.map((v, i) => {
        const h = 4 + Math.round(((v - min) / span) * 16);
        const isLatest = i === cells.length - 1;
        return (
          <div
            key={i}
            className={`k-spark__cell ${isLatest ? "is-latest" : ""}`}
            style={{ height: `${h}px` }}
          />
        );
      })}
    </div>
  );
}
