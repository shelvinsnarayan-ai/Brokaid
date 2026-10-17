/**
 * OperatorControlLayout — top bar + left rail + keyboard shortcuts
 * overlay ('?'). Wraps all /o/* routes for phase 7.
 */
import { Bell } from "../components/alerts/Bell";
import { useAlertsPoll } from "../state/alertsStore";
import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { signOut } from "../lib/authApi";
import { TenantSwitcher } from "../components/auth/Tenants";
import { BrandWordmark } from "../components/BrandWordmark";
import { KKbd } from "../components/ledger/KKbd";
import { AppFooter } from "../components/AppFooter";
import "../styles/app-footer.css";
import { useOperatorStore } from "../state/operatorStore";

// R0.6 (Addendum 2 §8.1): working roles see the work; Principal group for
// principal + operator; Operator group for the operator role only.
const NAV_GROUPS = [
  { title: null, roles: null as string[] | null, items: [
    { to: "/o/queue",       label: "Today",        kbd: "g q" },
    { to: "/o/clients",     label: "Clients",      kbd: "g c" },
    { to: "/o/renewals",    label: "Renewals",     kbd: "g r" },
    { to: "/o/inbox",       label: "Inbox",        kbd: "g i" },
  ] },
  { title: "Principal", roles: ["principal", "operator"], items: [
    { to: "/o",             label: "Overview",     kbd: "g o" },
    { to: "/o/weekly-pack", label: "Weekly pack",  kbd: "g w" },
    { to: "/o/shadow-mode", label: "Shadow Mode",  kbd: "g s" },
  ] },
  { title: "Operator", roles: ["operator"], items: [
    { to: "/o/governance",  label: "Agent operations", kbd: "g g" },
    { to: "/o/export",      label: "Export",       kbd: "g e" },
    { to: "/o/chase",       label: "Chase",        kbd: "g h" },
  ] },
];
const ROUTE_ROLES: [string, string[]][] = [
  ["/o/governance", ["operator"]], ["/o/export", ["operator"]], ["/o/chase", ["operator"]],
  ["/o/brief", ["principal", "operator"]], ["/o/weekly-pack", ["principal", "operator"]], ["/o/shadow-mode", ["principal", "operator"]],
];
const allowed = (p: string, role: string | undefined) => {
  if (p === "/o" || p === "/o/") return role === "principal" || role === "operator";
  const hit = ROUTE_ROLES.find(([prefix]) => p.startsWith(prefix));
  return !hit || hit[1].includes(role || "");
};

export function OperatorControlLayout() {
  useAlertsPoll();
  const navigate = useNavigate();
  const session = useOperatorStore((s) => s.session);
  const load = useOperatorStore((s) => s.loadSession);
  const toasts = useOperatorStore((s) => s.toasts);
  const [showHelp, setShowHelp] = useState(false);
  const [gPrefix, setGPrefix] = useState(false);
  const location = useLocation();
  const role = session?.session.active_role;
  const isPrincipal = role === "principal" || role === "operator";
  const NAV = NAV_GROUPS.filter((g) => !g.roles || g.roles.includes(role || ""));

  const [tried, setTried] = useState(false);
  useEffect(() => { if (!session) void load().finally(() => setTried(true)); }, [session, load]);
  useEffect(() => {
    if (tried && !session && !useOperatorStore.getState().loading) navigate("/login", { replace: true });
  }, [tried, session, navigate]);
  useEffect(() => {
    if (!session) return;
    if (!allowed(location.pathname, role)) navigate("/o/queue", { replace: true });
  }, [session, role, location.pathname, navigate]);

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
          o: "/o", q: "/o/queue", b: "/o/brief", c: "/o/clients", w: "/o/weekly-pack", r: "/o/renewals", i: "/o/inbox",
          s: "/o/shadow-mode", e: "/o/export", h: "/o/chase", g: "/o/governance",
        };
        const dest = map[e.key.toLowerCase()];
        if (dest && !allowed(dest, role)) { setGPrefix(false); return; }
        if (dest) { e.preventDefault(); navigate(dest); }
        setGPrefix(false);
        return;
      }
      if (e.key.toLowerCase() === "g" && !gPrefix) { setGPrefix(true); setTimeout(() => setGPrefix(false), 1500); }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [navigate, gPrefix, role]);

  async function logout() {
    await signOut();
    useOperatorStore.setState({ session: null });
    navigate("/login");
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
              <Bell base="/o" />
              <span data-testid="operator-user-label">
                <KKbd testId="user-role">{session.session.active_role}</KKbd>{" "}
                <strong>{session.user.display_name}</strong>
              </span>
              <TenantSwitcher testid="operator-tenant-switcher" />
              <Link to="/o/users" className="k-btn" data-testid="operator-users-link">People</Link>
              <Link to="/account/security" className="k-btn" data-testid="operator-security-link">Security</Link>
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
              ["g w", "Go to Weekly pack"],
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
