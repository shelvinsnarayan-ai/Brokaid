import { Link, useNavigate } from "react-router-dom";
import { useUnreadStore } from "../../state/unreadStore";
import { Ico, useC } from "./api";
import { ClientProfile } from "../ClientProfile";

const API = (import.meta as any).env.VITE_API_BASE || "";

export function Messages() {
  const unread = useUnreadStore((s) => s.count);
  return (
    <div data-testid="mer-messages">
      <header className="mer-head"><h1 className="mer-head__name mer-h">Messages</h1></header>
      <section className="mer-sec">
        <div className="mer-card">
          <Link to="/c/messages/broker" className="mer-row" data-testid="mer-thread-broker">
            <span className="mer-ico"><Ico name="chat" /></span>
            <span className="mer-row__main"><p className="mer-row__t">Your broker</p>
              <p className="mer-row__s">{unread ? `${unread} new ${unread === 1 ? "reply" : "replies"}` : "Ask anything about your cover"}</p></span>
            {unread ? <span className="mer-badge" data-testid="mer-thread-unread">{unread}</span> : null}
          </Link>
        </div>
        <p className="mer-row__s" style={{ margin: "12px 4px" }}>Claim updates are on each claim. Replies include your broker's general advice warning.</p>
      </section>
    </div>
  );
}

const MENU: [string, string, string][] = [
  ["documents", "Documents", "/c/documents"], ["notifications", "Notifications", "/c/alerts#settings"],
  ["security", "Security (password, 2FA, other devices)", "/account/security"], ["my-data", "My data and privacy", "/c/my-data"],
  ["complaint", "Make a complaint", "/c/complaint"], ["help", "Help — contact my broker", "/c/messages/broker"],
];

export function Profile() {
  const home = useC<{ client_name: string | null }>("c/home").data;
  const nav = useNavigate();
  const signOut = async () => {
    await fetch(`${API}/api/auth/logout`, { method: "POST", credentials: "include" }).catch(() => undefined);
    nav("/login");
  };
  return (
    <div data-testid="mer-profile">
      <header className="mer-head">
        <h1 className="mer-head__name mer-h">Profile</h1>
        <p className="mer-head__greet" data-testid="mer-profile-business">{home?.client_name || ""}</p>
      </header>
      <section className="mer-sec">
        <div className="mer-card">
          {MENU.map(([id, label, to]) => (
            <Link key={id} to={to} className="mer-row" data-testid={`mer-profile-${id}`}><span className="mer-row__main"><p className="mer-row__t">{label}</p></span>›</Link>
          ))}
          <button type="button" className="mer-row" style={{ width: "100%", border: 0, background: "none", textAlign: "left", cursor: "pointer", font: "inherit" }}
            onClick={signOut} data-testid="mer-profile-signout"><span className="mer-row__main"><p className="mer-row__t" style={{ color: "#B42318" }}>Sign out</p></span></button>
        </div>
      </section>
      <section className="mer-sec"><h2 className="mer-sec__title">Business details and people</h2></section>
      <div className="mer-legacy"><ClientProfile /></div>
    </div>
  );
}
