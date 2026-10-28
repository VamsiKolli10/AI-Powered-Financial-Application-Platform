import { Badge, EmptyState, Meter, Panel, PanelHeader } from "../components";
import { formatMoney } from "../lib/format";
import { summarySource } from "../lib/labels";
import { PERIOD_LABEL, type Period } from "../lib/period";
import type { Summary } from "../types";

type SpendingPanelProps = { summary: Summary | null; period: Period; currency: string; busy: boolean };

export function SpendingPanel({ summary, period, currency, busy }: SpendingPanelProps) {
  const source = summary ? summarySource(summary.summary_source) : null;
  const rows = summary?.by_category.slice(0, 6) ?? [];
  const max = Math.max(...rows.map((row) => Number(row.amount)), 0);
  const windowLabel = PERIOD_LABEL[period].current.toLowerCase();

  return (
    <Panel as="article" className="spending" busy={busy} labelledBy="spending-title">
      <PanelHeader
        eyebrow="Insights"
        title="Spending breakdown"
        titleId="spending-title"
        aside={source && <Badge description={source.description}>{source.label}</Badge>}
      />
      {!summary ? (
        <EmptyState>Insights are unavailable right now. Totals will appear when the service responds.</EmptyState>
      ) : rows.length === 0 ? (
        <EmptyState>No {currency} spending recorded {windowLabel} yet.</EmptyState>
      ) : (
        <>
          <p className="spending__summary">{summary.summary}</p>
          <ul className="spending__list">
            {rows.map((row) => (
              <li className="spending__row" key={row.category}>
                <div>
                  <span>{row.category}</span>
                  <strong>{formatMoney(row.amount, currency)}</strong>
                </div>
                <Meter value={Number(row.amount)} max={max} />
              </li>
            ))}
          </ul>
        </>
      )}
      {summary && summary.other_currencies.length > 0 && (
        <p className="spending__note">
          Totals are in {summary.currency}. Spending in {summary.other_currencies.join(", ")} is not included.
        </p>
      )}
    </Panel>
  );
}
