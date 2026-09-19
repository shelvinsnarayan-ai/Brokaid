import { KNumber } from "./KNumber";

interface _Component { key: string; label: string; value: number }
void ({} as _Component);

interface Props {
  components: {
    schema_validity: number;
    completeness: number;
    citation_existence: number;
    class_agreement: number;
  };
  scalar: number | null;
  source: string;
}

const ORDER: { key: keyof Props["components"]; label: string }[] = [
  { key: "schema_validity",   label: "Schema" },
  { key: "completeness",      label: "Completeness" },
  { key: "citation_existence", label: "Citations" },
  { key: "class_agreement",   label: "Class" },
];

function fillClass(v: number) {
  if (v >= 0.9) return "is-high";
  if (v < 0.8) return "is-low";
  return "";
}

export function KConfidenceBar({ components, scalar, source }: Props) {
  return (
    <div data-testid="k-confidence">
      {ORDER.map(({ key, label }) => {
        const v = components[key] ?? 0;
        return (
          <div className="k-conf-row" key={key} data-testid={`k-conf-${key}`}>
            <span className="k-conf-row__label">{label}</span>
            <div className="k-conf-row__bar">
              <div
                className={`k-conf-row__bar-fill ${fillClass(v)}`}
                style={{ width: `${Math.min(100, Math.max(0, v * 100))}%` }}
              />
            </div>
            <span className="k-conf-row__val">{v.toFixed(3)}</span>
          </div>
        );
      })}
      <div className="k-conf-scalar" data-testid="k-conf-scalar">
        <span style={{ color: "var(--slate)", fontSize: 11, letterSpacing: "0.06em", textTransform: "uppercase" }}>
          Scalar · min-of-components · {source}
        </span>
        <strong>
          <KNumber value={scalar} format="raw" />
        </strong>
      </div>
    </div>
  );
}
