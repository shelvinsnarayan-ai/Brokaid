/*! brokaid:dev-only */
export function DevAuthBanner() {
  return (
    <div
      className="brokaid-dev-banner"
      role="alert"
      data-testid="dev-auth-banner"
    >
      DEV AUTH — not for production
    </div>
  );
}
