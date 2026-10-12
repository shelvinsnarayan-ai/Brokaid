/** R0.6 — small inline glyph set (no "$ ! ?" characters as icons). */
const P: Record<string, string> = {
  home: "M3 11 12 4l9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z",
  shield: "M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z",
  plus: "M12 5v14M5 12h14",
  claim: "M6 3h9l4 4v14H6zM14 3v5h5M9 13h7M9 17h5",
  money: "M3 7h18v12H3zM3 11h18M7 15h3",
  user: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21c1-4 4-6 8-6s7 2 8 6",
};

export function Icon({ name, size = 20 }: { name: keyof typeof P | string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={P[name]} />
    </svg>
  );
}
