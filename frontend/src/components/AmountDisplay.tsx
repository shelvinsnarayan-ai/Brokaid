/**
 * AmountDisplay — currency rendered from the active jurisdiction pack.
 * The `Pack` is looked up via `usePack()`, so components don't hard-code
 * currency anywhere.
 */
import { usePack } from "../state/meStore";

interface AmountDisplayProps {
  amount: number;
  fractionDigits?: number;
  testId?: string;
}

export function AmountDisplay({
  amount, fractionDigits = 0, testId = "amount-display",
}: AmountDisplayProps) {
  const pack = usePack();
  if (!pack) {
    return <span data-testid={testId}>{amount.toFixed(fractionDigits)}</span>;
  }
  const formatted = new Intl.NumberFormat(pack.locale, {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  }).format(amount);
  return (
    <span
      data-testid={testId}
      data-currency={pack.currency_code}
      data-locale={pack.locale}
      className="brokaid-amount"
    >
      {pack.currency_symbol}
      {formatted} {pack.currency_code}
    </span>
  );
}
