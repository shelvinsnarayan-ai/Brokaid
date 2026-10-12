/**
 * Profile tab — user info, jurisdiction switcher (dev-only), Add to
 * Home Screen CTA, logout.
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { isPreviewMode, useMeStore, usePack } from "../state/meStore";

export function ClientProfile() {
  const nav = useNavigate();
  const me = useMeStore((s) => s.me);
  const pack = usePack();
  const setPreview = useMeStore((s) => s.setPreview);
  const logout = useMeStore((s) => s.logout);
  const preview = me?.client_prefs?.jurisdiction ?? null;
  const [installEvt, setInstallEvt] = useState<any>(null);

  useEffect(() => {
    const h = (e: Event) => {
      e.preventDefault();
      setInstallEvt(e);
    };
    window.addEventListener("beforeinstallprompt", h);
    return () => window.removeEventListener("beforeinstallprompt", h);
  }, []);

  async function addToHome() {
    if (installEvt) {
      installEvt.prompt();
      await installEvt.userChoice;
      setInstallEvt(null);
    } else {
      alert(
        "iOS Safari: tap Share → Add to Home Screen. " +
          "The app icon lands on your Home Screen."
      );
    }
  }

  async function doLogout() {
    await logout();
    nav("/c/login");
  }

  return (
    <section className="brokaid-tab" data-testid="tab-profile-root">
      <header className="brokaid-tab__head">
        <p className="brokaid-tab__eyebrow">Profile</p>
        <h1 className="brokaid-tab__title">Your account</h1>
      </header>

      <section className="brokaid-profile__block" data-testid="profile-user">
        <div className="brokaid-profile__row">
          <span>Name</span>
          <strong>{me?.user?.display_name ?? "—"}</strong>
        </div>
        <div className="brokaid-profile__row">
          <span>Email</span>
          <span>{me?.user?.email ?? "—"}</span>
        </div>
        {me?.linked_client && (
          <>
            <div className="brokaid-profile__row">
              <span>Client</span>
              <span>{me.linked_client.display_name}</span>
            </div>
            <div className="brokaid-profile__row">
              <span>Jurisdiction</span>
              <span>
                {me.linked_client.jurisdiction_code}
                {isPreviewMode(me) && (
                  <>
                    {" "}
                    · previewing {preview}{" "}
                  </>
                )}
              </span>
            </div>
          </>
        )}
      </section>

      {import.meta.env.BROKAID_AUTH_DEV_MODE && (
        <section
          className="brokaid-profile__block"
          data-testid="profile-jurisdiction-switch"
        >
          <h2 className="brokaid-profile__h2">
            Jurisdiction preview <span className="brokaid-mini">dev-only</span>
          </h2>
          <div className="brokaid-jurisdiction-switch">
            <button
              type="button"
              className={preview === null ? "is-active" : ""}
              onClick={() => setPreview(null)}
              data-testid="jurisdiction-clear"
            >
              Actual ({me?.linked_client?.jurisdiction_code ?? "AU"})
            </button>
            <button
              type="button"
              className={preview === "AU" ? "is-active" : ""}
              onClick={() => setPreview("AU")}
              data-testid="jurisdiction-au"
            >
              AU
            </button>
            <button
              type="button"
              className={preview === "SG" ? "is-active" : ""}
              onClick={() => setPreview("SG")}
              data-testid="jurisdiction-sg"
            >
              SG
            </button>
          </div>
        </section>
      )}

      <section className="brokaid-profile__block">
        <h2 className="brokaid-profile__h2">Install</h2>
        <button
          type="button"
          className="brokaid-btn brokaid-btn--primary"
          onClick={addToHome}
          data-testid="add-to-home"
        >
          Add to Home Screen
        </button>
      </section>

      {pack && (
        <footer
          className="brokaid-disclosure"
          data-testid="profile-disclosure"
        >
          <strong>Privacy notice.</strong>{" "}
          {pack.disclosure_strings.privacy_notice}
          <br />
          <strong>Financial Services Guide.</strong>{" "}
          {pack.disclosure_strings.fsg}
          <div className="brokaid-disclosure__reg">
            Regulated by {pack.regulator} · {pack.display_name}
          </div>
        </footer>
      )}

      <button
        type="button"
        className="brokaid-btn brokaid-btn--secondary"
        onClick={doLogout}
        data-testid="profile-logout"
      >
        Sign out
      </button>
    </section>
  );
}
