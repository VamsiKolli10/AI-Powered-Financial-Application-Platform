const moneyFormatters = new Map<string, Intl.NumberFormat>();

function moneyFormatter(currency: string): Intl.NumberFormat {
  const code = currency.toUpperCase();
  let formatter = moneyFormatters.get(code);
  if (!formatter) {
    try {
      formatter = new Intl.NumberFormat(undefined, { style: "currency", currency: code });
    } catch {
      // Unknown ISO code: fall back to a plain number with the code appended.
      formatter = new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    moneyFormatters.set(code, formatter);
  }
  return formatter;
}

/** Format an amount in its own currency. API amounts arrive as decimal strings. */
export function formatMoney(value: string | number, currency = "USD"): string {
  const formatter = moneyFormatter(currency);
  const text = formatter.format(Number(value));
  return formatter.resolvedOptions().style === "currency" ? text : `${text} ${currency.toUpperCase()}`;
}

// Ledger dates and reporting periods use UTC, including date-only CSV/manual entries.
const dayFormat = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", timeZone: "UTC" });

export function formatDay(iso: string): string {
  return dayFormat.format(new Date(iso));
}

export function formatChange(pct: number | null | undefined, comparedTo: string): string | null {
  if (pct == null) return null;
  const sign = pct > 0 ? "+" : pct < 0 ? "−" : "";
  return `${sign}${Math.abs(pct).toFixed(1)}% vs ${comparedTo}`;
}

/** The currency most records use; aggregates without a currency are shown in it. */
export function dominantCurrency(records: Array<{ currency: string }>, fallback = "USD"): string {
  const counts = new Map<string, number>();
  for (const { currency } of records) counts.set(currency, (counts.get(currency) ?? 0) + 1);
  let best = fallback;
  let bestCount = 0;
  for (const [currency, count] of counts) {
    if (count > bestCount) {
      best = currency;
      bestCount = count;
    }
  }
  return best;
}

export function greeting(now: Date, fullName?: string | null): string {
  const hour = now.getHours();
  const part = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  const firstName = fullName?.trim().split(/\s+/)[0];
  return firstName ? `${part}, ${firstName}.` : `${part}.`;
}
