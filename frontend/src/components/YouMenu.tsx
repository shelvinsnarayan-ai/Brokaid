/** R0.6 — You menu behind the avatar (Addendum 2 §5.1). */
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMeStore } from "../state/meStore";

const ITEMS = [
  { id: "people", label: "People", to: "/c/profile" },
  { id: "documents", label: "Documents", to: "/c/cover" },
  { id: "messages", label: "Messages", to: "/c/ask" },
  { id: "notifications", label: "Notifications", to: "/c/profile" },
  { id: "settings", label: "Settings", to: "/c/profile" },
  { id: "fsg", label: "FSG", to: "/c/profile" },
  { id: "privacy", label: "Privacy", to: "/c/profile" },
  { id: "complaint", label: "Make a complaint", to: "/c/ask" },
];

export function YouMenu() {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const me = useMeStore((s) => s.me);
  const logout = useMeStore((s) => s.logout);
  const initials = (me?.user?.display_name || "You").split(" ").map((w) => w[0]).join("").slice(0, 2).toUpperCase();
  return (
    <div className="brokaid-you">
      <button type="button" className="brokaid-you__avatar" onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu" aria-expanded={open} data-testid="you-avatar">{initials}</button>
      {open && (
        <ul className="brokaid-you__menu" role="menu" data-testid="you-menu">
          {ITEMS.map((i) => (
            <li key={i.id}>
              <button type="button" role="menuitem" data-testid={`you-${i.id}`}
                onClick={() => { setOpen(false); navigate(i.to); }}>{i.label}</button>
            </li>
          ))}
          <li>
            <button type="button" role="menuitem" data-testid="you-signout"
              onClick={async () => { setOpen(false); await logout(); navigate("/c/login"); }}>Sign out</button>
          </li>
        </ul>
      )}
    </div>
  );
}
