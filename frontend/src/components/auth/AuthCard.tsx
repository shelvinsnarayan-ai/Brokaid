import { ReactNode } from "react";
import { BrandWordmark } from "../BrandWordmark";
import "../../styles/auth.css";

export function AuthCard({ title, lead, children, testid }: { title: string; lead?: ReactNode; children: ReactNode; testid: string }) {
  return (
    <main className="auth-page" data-testid={testid}>
      <div className="auth-card">
        <BrandWordmark variant="ledger-light" height={26} />
        <h1 className="auth-card__title">{title}</h1>
        {lead ? <p className="auth-card__lead">{lead}</p> : null}
        {children}
      </div>
    </main>
  );
}

export function AuthError({ msg, testid }: { msg: string | null; testid: string }) {
  return msg ? <p className="auth-err" role="alert" data-testid={testid}>{msg}</p> : null;
}
