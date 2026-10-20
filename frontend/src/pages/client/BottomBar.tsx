import { useState } from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { useUnreadStore } from "../../state/unreadStore";
import { Ico } from "./api";

const ACTIONS = [
  { id: "claim", label: "Make a claim", to: "/c/claims/new", icon: "claim" },
  { id: "change", label: "Change my cover", to: "/c?pick=change", icon: "change" },
  { id: "certificate", label: "Get a certificate", to: "/c?pick=certificate", icon: "cert" },
  { id: "ask", label: "Ask my broker", to: "/c/messages/broker", icon: "chat" },
  { id: "pay", label: "Pay a bill", to: "/c/activity?f=payments", icon: "pay" },
];

export function BottomBar() {
  const [open, setOpen] = useState(false);
  const unread = useUnreadStore((s) => s.count);
  const nav = useNavigate();
  const tab = (to: string, label: string, end = false, badge = 0) => (
    <NavLink to={to} end={end} className={({ isActive }) => `mer-tab${isActive ? " mer-tab--on" : ""}`} data-testid={`mer-tab-${label.toLowerCase()}`}>
      <Ico name={{ Home: "house", Activity: "list", Messages: "chat", Profile: "user" }[label] || "shield"} />
      {label}
      {badge ? <span className="mer-dot" data-testid="mer-messages-badge">{badge}</span> : null}
    </NavLink>
  );
  return (
    <>
      <nav className="mer-bar" aria-label="Main" data-testid="mer-bottom-bar">
        <div className="mer-bar__in">
          {tab("/c", "Home", true)}
          {tab("/c/activity", "Activity")}
          <button type="button" className="mer-plus" aria-label="New: claim, change, certificate, message or payment"
            onClick={() => setOpen(true)} data-testid="mer-plus"><Ico name="plus" /></button>
          {tab("/c/messages", "Messages", false, unread)}
          {tab("/c/profile", "Profile")}
        </div>
      </nav>
      {open ? (
        <div className="mer-sheet-bg" role="dialog" aria-modal="true" aria-label="What do you need?" onClick={() => setOpen(false)} data-testid="mer-action-sheet">
          <div className="mer-sheet" onClick={(e) => e.stopPropagation()}>
            <p className="mer-h" style={{ fontWeight: 700, margin: "0 8px 6px" }}>What do you need?</p>
            {ACTIONS.map((a) => (
              <button key={a.id} type="button" className="mer-sheet__item" data-testid={`mer-action-${a.id}`}
                onClick={() => { setOpen(false); nav(a.to); }}><span className="mer-ico"><Ico name={a.icon} /></span>{a.label}</button>
            ))}
            <Link to="#" className="mer-sheet__item" style={{ justifyContent: "center", color: "#5B6478" }}
              onClick={(e) => { e.preventDefault(); setOpen(false); }} data-testid="mer-action-close">Close</Link>
          </div>
        </div>
      ) : null}
    </>
  );
}
