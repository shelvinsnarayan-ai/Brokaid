import { Link, useNavigate } from "react-router-dom";
import { useUnreadStore } from "../../state/unreadStore";
import { Ico, useC } from "./api";
import { ClientProfile } from "../ClientProfile";
import { People } from "./People";

const API = (import.meta as any).env.VITE_API_BASE || "";

interface ClaimRow { claim_id: string; claim_reference: string; label: string; stage_label: string; open: boolean;
  thread_id: string | null; last_message: string | null; last_at: string | null; unread: number }

const when = (iso: string | null) => iso ? new Date(iso).toLocaleDateString("en-AU", { day: "numeric", month: "short" }) : "";

function ClaimThreadRow({ r }: { r: ClaimRow }) {
  return (
    <Link to={`/c/messages/claim/${r.claim_id}`} className="mer-row" data-testid={`mer-thread-claim-${r.claim_id}`}>
      <span className="mer-ico"><Ico name="claim" /></span>
      <span className="mer-row__main"><p className="mer-row__t">{r.label}</p>
        <p className="mer-row__s mer-clamp">{r.stage_label} · {r.last_message || "Start a conversation about this claim"}</p></span>
      <span className="mer-fig">{r.last_at ? <p className="mer-fig__l">{when(r.last_at)}</p> : null}
        {r.unread ? <span className="mer-badge" data-testid={`mer-thread-claim-unread-${r.claim_id}`}>{r.unread}</span> : null}</span>
    </Link>
  );
}

export function Messages() {
  const threads = useUnreadStore((s) => s.threads);
  const unread = threads.filter((t) => !t.claim_id).reduce((n, t) => n + t.unread, 0);
  const claims = useC<{ threads: ClaimRow[] }>("c/claim-threads").data?.threads;
  const open = (claims || []).filter((r) => r.open);
  const earlier = (claims || []).filter((r) => !r.open);
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
          {open.map((r) => <ClaimThreadRow key={r.claim_id} r={r} />)}
        </div>
        {earlier.length ? (
          <div data-testid="mer-threads-earlier">
            <p className="mer-group">Earlier claims</p>
            <div className="mer-card">{earlier.map((r) => <ClaimThreadRow key={r.claim_id} r={r} />)}</div>
          </div>
        ) : null}
        <p className="mer-row__s" style={{ margin: "12px 4px" }}>Replies include your broker's general advice warning where it applies.</p>
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
      <People />
      <section className="mer-sec"><h2 className="mer-sec__title">Business details</h2></section>
      <div className="mer-legacy"><ClientProfile /></div>
    </div>
  );
}
