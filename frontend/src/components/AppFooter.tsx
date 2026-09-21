/**
 * AppFooter — runtime SHA footer.
 *
 * Fetches /api/build-info once and renders the short SHA in a low-
 * weight footer. Rendered on both client and operator shells so every
 * page shows the deployed SHA (Build Brief v2.1 §8 R1 #1).
 *
 * Deliberately runtime, not build-time: the operator must be able to
 * verify that the SHA shown in the footer matches the pushed tip in
 * production, and Vite compile-time constants go stale when the
 * deployed bundle is served without a fresh build.
 */
import { useEffect, useState } from "react";

type BuildInfo = {
  sha: string | null;
  short_sha: string | null;
  branch: string | null;
  built_at: string | null;
  env: string | null;
  llm_key_present: boolean;
};

type Variant = "harbour" | "ledger";

export function AppFooter({ variant }: { variant: Variant }) {
  const [info, setInfo] = useState<BuildInfo | null>(null);

  useEffect(() => {
    let alive = true;
    fetch("/api/build-info", { credentials: "omit" })
      .then((r) => (r.ok ? r.json() : null))
      .then((b) => {
        if (alive && b) setInfo(b);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  const sha = info?.short_sha ?? "…";
  const env = info?.env ?? "";
  const cls =
    variant === "harbour" ? "brokaid-app-footer brokaid-app-footer--harbour" : "brokaid-app-footer brokaid-app-footer--ledger";
  return (
    <footer
      className={cls}
      role="contentinfo"
      data-testid="app-footer"
      aria-label={`Build ${sha}`}
    >
      <span className="brokaid-app-footer__label">build</span>
      <span className="brokaid-app-footer__sha" data-testid="app-footer-sha">
        {sha}
      </span>
      {env && env !== "production" ? (
        <>
          <span className="brokaid-app-footer__sep" aria-hidden="true">
            ·
          </span>
          <span
            className="brokaid-app-footer__env"
            data-testid="app-footer-env"
          >
            {env}
          </span>
        </>
      ) : null}
    </footer>
  );
}
