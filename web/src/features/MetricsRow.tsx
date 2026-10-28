import { Metric } from "../components";
import { formatChange, formatMoney } from "../lib/format";
import { PERIOD_LABEL, type Period } from "../lib/period";
import type { NotificationList, PeriodTransactions, Summary } from "../types";

type MetricsRowProps = {
  period: Period;
  currency: string;
  periodTransactions: PeriodTransactions | null;
  summary: Summary | null;
  notifications: NotificationList | null;
};

/**
 * Every money figure covers the same window: the selected period to date.
 * Money out is the Insights total (outflows) so it matches the breakdown beside it;
 * money in is summed from the same period's transactions; cash flow is in − out.
 */
export function MetricsRow({ period, currency, periodTransactions, summary, notifications }: MetricsRowProps) {
  const labels = PERIOD_LABEL[period];
  const items = periodTransactions?.items ?? [];
  const sameCurrency = items.filter((tx) => tx.currency === currency);
  // Other currencies are reported separately, never added into these totals.
  const mixedCurrencies = sameCurrency.length !== items.length || (summary?.other_currencies.length ?? 0) > 0;

  const credits = sameCurrency.filter((tx) => Number(tx.amount) > 0);
  const moneyIn = periodTransactions ? credits.reduce((sum, tx) => sum + Number(tx.amount), 0) : null;
  const moneyOut = summary
    ? Number(summary.total_spend)
    : periodTransactions
      ? sameCurrency.filter((tx) => Number(tx.amount) < 0).reduce((sum, tx) => sum - Number(tx.amount), 0)
      : null;
  const cashFlow = moneyIn !== null && moneyOut !== null ? moneyIn - moneyOut : null;

  const money = (value: number | null) => (value === null ? null : formatMoney(value, currency));
  const scopeNote = periodTransactions?.truncated ? " (partial)" : "";

  return (
    <section id="overview" className="metrics" aria-label={`${labels.current} at a glance`}>
      <Metric
        label="Cash flow"
        value={money(cashFlow)}
        caption={
          cashFlow === null
            ? "Unavailable"
            : `${labels.current}${scopeNote} · ${mixedCurrencies ? `${currency} only` : "in minus out"}`
        }
      />
      <Metric
        label="Money in"
        value={money(moneyIn)}
        caption={
          moneyIn === null
            ? "Transactions unavailable"
            : `${credits.length} credit${credits.length === 1 ? "" : "s"} ${labels.current.toLowerCase()}`
        }
      />
      <Metric
        label="Money out"
        value={money(moneyOut)}
        caption={formatChange(summary?.change_vs_previous_pct, labels.previous) ?? labels.current}
      />
      <Metric
        label="Unread alerts"
        tone="alert"
        value={notifications ? String(notifications.unread_count) : null}
        caption={notifications ? "Rules-based monitoring" : "Alerts unavailable"}
      />
    </section>
  );
}
