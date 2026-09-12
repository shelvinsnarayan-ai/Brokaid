/**
 * TabBar — bottom nav on mobile, side rail on desktop (>= 768px).
 * Six tabs. Keyboard navigable (native <Link> semantics).
 */
import { NavLink } from "react-router-dom";

interface Tab {
  path: string;
  label: string;
  glyph: string;
  testId: string;
}

const TABS: Tab[] = [
  { path: "/c",         label: "Home",    glyph: "◈", testId: "tab-home" },
  { path: "/c/cover",   label: "Cover",   glyph: "◉", testId: "tab-cover" },
  { path: "/c/money",   label: "Money",   glyph: "$", testId: "tab-money" },
  { path: "/c/claims",  label: "Claims",  glyph: "!", testId: "tab-claims" },
  { path: "/c/ask",     label: "Ask",     glyph: "?", testId: "tab-ask" },
  { path: "/c/profile", label: "Profile", glyph: "◐", testId: "tab-profile" },
];

export function TabBar() {
  return (
    <nav
      className="brokaid-tabbar"
      role="navigation"
      aria-label="Client sections"
      data-testid="client-tabbar"
    >
      {TABS.map((t) => (
        <NavLink
          key={t.path}
          to={t.path}
          end={t.path === "/c"}
          className={({ isActive }) =>
            `brokaid-tabbar__item ${isActive ? "is-active" : ""}`
          }
          data-testid={t.testId}
        >
          <span className="brokaid-tabbar__glyph" aria-hidden>
            {t.glyph}
          </span>
          <span className="brokaid-tabbar__label">{t.label}</span>
        </NavLink>
      ))}
    </nav>
  );
}
