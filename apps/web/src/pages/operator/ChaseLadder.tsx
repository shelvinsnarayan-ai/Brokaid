/**
 * Phase-18 preview stub — Chase Ladder (Ledger palette).
 *
 * Read-only, five empty-state cards, one per chase category:
 * money / information / instruction / insurer / signature.
 *
 * NO backend route. The category list is a hard-coded frontend
 * constant. Engine + queue schema land post-SIRC.
 *
 * Icons: inline stroke-only SVG (Lucide-style: 24×24, stroke-width
 * 1.75, currentColor). Zero-dep — no `lucide-react` install needed
 * for a five-icon preview stub. Same visual family as the icon set
 * we can standardise on post-SIRC.
 */
type IconProps = { size?: number };

const Icon = {
  Money: ({ size = 20 }: IconProps) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="1.75" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <rect x="2" y="6" width="20" height="12" rx="2" />
      <circle cx="12" cy="12" r="2.5" />
      <path d="M6 12h.01M18 12h.01" />
    </svg>
  ),
  Info: ({ size = 20 }: IconProps) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="1.75" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <rect x="4" y="3" width="16" height="18" rx="2" />
      <path d="M8 8h8M8 12h8M8 16h5" />
    </svg>
  ),
  Instruction: ({ size = 20 }: IconProps) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="1.75" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <path d="M6 3h9l4 4v14a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z" />
      <path d="M15 3v4h4" />
      <path d="M9 13h6M9 17h4" />
    </svg>
  ),
  Insurer: ({ size = 20 }: IconProps) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="1.75" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <path d="M3 21V8l9-5 9 5v13" />
      <path d="M9 21v-6h6v6" />
      <path d="M3 21h18" />
    </svg>
  ),
  Signature: ({ size = 20 }: IconProps) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="1.75" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <path d="M4 18c4-6 6-8 8-8 3 0 2 4 4 4 1 0 2-1 3-2" />
      <path d="M4 22h16" />
    </svg>
  ),
} as const;

type Category = {
  key: string;
  label: string;
  summary: string;
  Icon: (p: IconProps) => JSX.Element;
};

const CATEGORIES: readonly Category[] = [
  {
    key: "money",
    label: "Money",
    summary: "Chase premium receipts, refunds, and short-pays.",
    Icon: Icon.Money,
  },
  {
    key: "information",
    label: "Information",
    summary: "Chase outstanding client answers, evidence, and docs.",
    Icon: Icon.Info,
  },
  {
    key: "instruction",
    label: "Instruction",
    summary: "Chase pending client sign-offs on operator recommendations.",
    Icon: Icon.Instruction,
  },
  {
    key: "insurer",
    label: "Insurer",
    summary: "Chase carrier turnaround on submissions and endorsements.",
    Icon: Icon.Insurer,
  },
  {
    key: "signature",
    label: "Signature",
    summary: "Chase e-sign completions on issued documents.",
    Icon: Icon.Signature,
  },
];

export function ChaseLadder() {
  return (
    <section
      className="ledger-chase"
      data-testid="operator-chase-root"
      aria-labelledby="chase-heading"
    >
      <header className="ledger-chase__head">
        <p className="ledger-main__eyebrow">Chase</p>
        <h1 id="chase-heading" className="ledger-chase__title">
          Chase ladder
        </h1>
        <p className="ledger-chase__lede">
          Preview only. The chase-ladder engine lands post-SIRC. Categories
          below reflect the five chase types operators will see.
        </p>
      </header>

      <ol
        className="ledger-chase__grid"
        data-testid="chase-category-grid"
        aria-label="Chase categories"
      >
        {CATEGORIES.map((c) => (
          <li
            key={c.key}
            className="ledger-chase-card"
            data-testid={`chase-card-${c.key}`}
          >
            <div className="ledger-chase-card__icon" aria-hidden={true}>
              <c.Icon size={20} />
            </div>
            <div className="ledger-chase-card__body">
              <h2 className="ledger-chase-card__label">{c.label}</h2>
              <p className="ledger-chase-card__summary">{c.summary}</p>
              <span
                className="ledger-chase-card__pill"
                data-testid={`chase-pill-${c.key}`}
              >
                Coming post-SIRC
              </span>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
