/**
 * TabBar (R0.6, Addendum 2 §5.1) — Home · Cover · ⊕ Do · Claims · Money.
 * Do is raised in the centre and opens the Do sheet from any client screen.
 */
import { useState } from "react";
import { NavLink } from "react-router-dom";
import { DoSheet } from "./DoSheet";
import { Icon } from "./Icon";
import { UnreadDot, useUnreadStore } from "../state/unreadStore";

const TABS = [
  { path: "/c", label: "Home", icon: "home", testId: "tab-home" },
  { path: "/c/cover", label: "Cover", icon: "shield", testId: "tab-cover" },
  null,
  { path: "/c/claims", label: "Claims", icon: "claim", testId: "tab-claims" },
  { path: "/c/money", label: "Money", icon: "money", testId: "tab-money" },
];

export function TabBar() {
  const [open, setOpen] = useState(false);
  const unread = useUnreadStore((s) => s.count);
  return (
    <>
      <nav className="brokaid-tabbar" role="navigation" aria-label="Client sections" data-testid="client-tabbar">
        {TABS.map((t) =>
          t ? (
            <NavLink key={t.path} to={t.path} end={t.path === "/c"}
              className={({ isActive }) => `brokaid-tabbar__item ${isActive ? "is-active" : ""}`}
              data-testid={t.testId}>
              <span className="brokaid-tabbar__glyph"><Icon name={t.icon} /></span>
              <span className="brokaid-tabbar__label">{t.label}</span>
            </NavLink>
          ) : (
            <button key="do" type="button" className="brokaid-tabbar__item brokaid-tabbar__do"
              onClick={() => setOpen(true)} aria-haspopup="dialog" data-testid="tab-do">
              <span className="brokaid-tabbar__do-disc"><Icon name="plus" size={24} /><UnreadDot n={unread} testid="tab-do-unread-dot" /></span>
              <span className="brokaid-tabbar__label">Do</span>
            </button>
          ),
        )}
      </nav>
      {open && <DoSheet onClose={() => setOpen(false)} />}
    </>
  );
}
