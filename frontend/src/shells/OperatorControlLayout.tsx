/**
 * OperatorControlLayout — top bar + left rail + keyboard shortcuts
 * overlay ('?'). Wraps all /o/* routes for phase 7.
 */
import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { BrandWordmark } from "../components/BrandWordmark";
import { KKbd } from "../components/ledger/KKbd";
import { AppFooter } from "../components/AppFooter";
import "../styles/app-footer.css";
import { useOperatorStore } from "../state/operatorStore";

// R0.6 (Addendum 2 §8.1): working roles see the work; Principal and Operator
// groups are shown to the principal (Ada is the demo Operator).
const NAV_GROUPS = [
  { title: null, principalOnly: false, items: [
    { to: "/o/queue",       label: "Today",        kbd: "g q" },
    { to: "/o/clients",     label: "Clients",      kbd: "g c" },
  ] },
  { title: "Principal", principalOnly: true, items: [
    { to: "/o",             label: "Overview",     kbd: "g o" },
    { to: "/o/brief",       label: "Daily Brief",  kbd: "g b" },
    { to: "/o/shadow-mode", label: "Shadow Mode",  kbd: "g s" },
  ] },
  { title: "Operator", principalOnly: true, items: [
    { to: "/o/governance",  label: "Agent operations", kbd: "g g" },
    { to: "/o/export",      label: "Export",       kbd: "g e" },
    { to: "/o/chase",       label: "Chase",        kbd: "g h" },
  ] },
];
const OPERATOR_ONLY = ["/o/brief", "/o/shadow-mode", "/o/governance", "/o/export", "/o/chase"];

export function OperatorControlLayout() {
  const navigate = useNavigate();
  const session = useOperatorStore((s) => s.session);
  const load = useOperatorStore((s) => s.loadSession);
  const toasts = useOperatorStore((s) => s.toasts);
  const [showHelp, setShowHelp] = useState(false);
  const [gPrefix, setGPrefix] = useState(false);
  const location = useLocation();
  const isPrincipal = session?.session.active_role === "principal";
  const NAV = NAV_GROUPS.filter((g) => isPrincipal || !g.principalOnly);

  useEffect(() => { if (!session) void load(); }, [session, load]);
  useEffect(() => {
    if (!session || isPrincipal) return;
    const p = location.pathname;
    if (p === "/o" || p === "/o/" || OPERATOR_ONLY.some((x) => p.startsWith(x))) navigate("/o/queue", { replace: true });
  }, [session, isPrincipal, location.pathname, navigate]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // Skip if focused in an editable field.
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName?.toLowerCase();
      const editable = tag === "input" || tag === "textarea" || (target as any)?.isContentEditable;

      if (e.key === "?" && !editable) {
        e.preventDefault();
        setShowHelp((v) => !v);
        return;
      }
      if (e.key === "Escape") { setShowHelp(false); setGPrefix(false); return; }
      if (editable) return;

      if (gPrefix) {
        const map: Record<string, string> = {
          o: "/o", q: "/o/queue", b: "/o/brief", c: "/o/clients",
          s: "/o/shadow-mode", e: "/o/export", h: "/o/chase", g: "/o/governance",
        };
        const dest = map[e.key.toLowerCase()];
        if (dest && !isPrincipal && (dest === "/o" || OPERATOR_ONLY.includes(dest))) { setGPrefix(false); return; }
        if (dest) { e.preventDefault(); navigate(dest); }
        setGPrefix(false);
        return;
      }
      if (e.key.toLowerCase() === "g" && !gPrefix) { setGPrefix(true); setTimeout(() => setGPrefix(false), 1500); }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [navigate, gPrefix, isPrincipal]);

  async function logout() {
    document.cookie = "brokaid_session=; Max-Age=0; path=/";
    navigate("/o/login");
  }

  return (
    <div className="ledger-app surface-ledger" data-testid="operator-control-root">
      <header className="ledger-topbar" data-testid="operator-topbar">
        <Link to={isPrincipal ? "/o" : "/o/queue"} className="ledger-topbar__brand" data-testid="brand-mark" aria-label="brokAId — go to start">
          <img src="/brand/mark-ring-reversed.svg" height={22} width={22} alt="" data-testid="brand-ring-mark" style={{ marginRight: 8 }} />
          <BrandWordmark variant="ledger-reverse" height={22} />
        </Link>
        <div className="ledger-topbar__tenant" data-testid="operator-tenant">
          {session ? <>Harbourline Insurance Brokers <span className="ledger-topbar__demo" data-testid="operator-demo-badge">Demo</span></> : "loading…"}
        </div>
        <div className="ledger-topbar__user">
          <button
            className="ledger-topbar__cmd"
            onClick={() => setShowHelp(true)}
            data-testid="cmd-help"
            aria-label="Show keyboard shortcuts"
          >
            ⌘K · shortcuts (?)
          </button>
          {session && (
            <>
              <span data-testid="operator-user-label">
                <KKbd testId="user-role">{session.session.active_role}</KKbd>{" "}
                <strong>{session.user.display_name}</strong>
              </span>
              <button className="k-btn" onClick={logout} data-testid="operator-logout">Sign out</button>
            </>
          )}
        </div>
      </header>

      <div className="ledger-shell">
        <nav className="ledger-rail" data-testid="operator-rail">
          {NAV.map((g) => (
            <div key={g.title || "work"} className="ledger-rail__group" data-testid={`rail-group-${(g.title || "work").toLowerCase()}`}>
            {g.title && <div className="ledger-rail__group-title">{g.title}</div>}
            {g.items.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.to === "/o"}
              className={({ isActive }) => `ledger-rail__link ${isActive ? "is-active" : ""}`}
              data-testid={`rail-link-${n.label.toLowerCase().replace(/\s+/g, "-")}`}
            >
              <span>{n.label}</span>
              <span className="ledger-rail__kbd">{n.kbd}</span>
            </NavLink>
            ))}
            </div>
          ))}
          <div className="ledger-rail__hint">
            Press <KKbd>?</KKbd> for the full keyboard map.<br/>
            Prefix <KKbd>g</KKbd> to jump.
          </div>
        </nav>

        <main className="ledger-main" data-testid="operator-main">
          <Outlet />
        </main>
      </div>

      {showHelp && (
        <div className="k-overlay" onClick={() => setShowHelp(false)} data-testid="shortcuts-overlay">
          <div className="k-overlay__card" onClick={(e) => e.stopPropagation()}>
            <h2>Keyboard shortcuts</h2>
            {[
              ["? ", "Toggle this overlay"],
              ["Escape", "Dismiss overlay / clear prefix"],
              ["g o", "Go to Overview"],
              ["g q", "Go to Queue"],
              ["g b", "Go to Daily Brief"],
              ["g c", "Go to Clients"],
              ["g s", "Go to Shadow Mode"],
              ["g e", "Go to Export"],
              ["g h", "Go to Chase (preview)"],
              ["g g", "Go to Governance"],
              ["j / k", "Move selection down / up (Queue)"],
              ["Enter", "Open selected row"],
              ["a", "Approve selected"],
              ["e", "Edit selected"],
              ["r", "Reject selected"],
              ["b", "Block selected (compliance role only)"],
            ].map(([k, v]) => (
              <div className="k-overlay__row" key={k} data-testid={`shortcut-row-${k.replace(/\s+/g, "-")}`}>
                <KKbd>{k}</KKbd><span>{v}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {toasts.map((t) => (
        <div key={t.id} className={`k-toast k-toast--${t.kind}`} data-testid={`toast-${t.id}`}>
          {t.message}
        </div>
      ))}

      <AppFooter variant="ledger" />
    </div>
  );
}
