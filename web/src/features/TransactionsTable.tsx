import { useEffect, useState } from "react";
import { ANOMALY_REVIEW_THRESHOLD, ApiError, getCategories } from "../api";
import { Badge, EmptyState, Panel, PanelHeader } from "../components";
import { formatDay, formatMoney } from "../lib/format";
import { categorySource } from "../lib/labels";
import type { Transaction } from "../types";
import { CategoryEditor } from "./CategoryEditor";

export function TransactionsTable({ transactions, token, onChanged, onUnauthorized }: {
  transactions: Transaction[] | null; token?: string; onChanged?: () => void; onUnauthorized?: () => void;
}) {
  const [categories, setCategories] = useState<Record<string, string>>({});
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    getCategories(token).then((result) => { if (!cancelled) setCategories(result); }).catch((reason) => {
      if (!cancelled && reason instanceof ApiError && reason.status === 401) onUnauthorized?.();
    });
    return () => { cancelled = true; };
  }, [token, onUnauthorized]);
  return (
    <Panel id="transactions" className="transactions" labelledBy="transactions-title">
      <PanelHeader
        eyebrow="Activity"
        title="Recent transactions"
        titleId="transactions-title"
        aside={transactions && <span className="panel-header__count">{transactions.length} shown</span>}
      />
      {!transactions ? (
        <EmptyState>Transactions are unavailable right now.</EmptyState>
      ) : transactions.length === 0 ? (
        <EmptyState>No transactions yet. New ones appear here as soon as they are posted.</EmptyState>
      ) : (
        <div className="data-table__scroll">
          <table className="data-table">
            <caption className="sr-only">Recent transactions, newest first</caption>
            <thead>
              <tr>
                <th scope="col">Merchant</th>
                <th scope="col">Category</th>
                <th scope="col" className="data-table__col--source">Source</th>
                <th scope="col" className="data-table__col--date">Date</th>
                <th scope="col" className="data-table__col--amount">Amount</th>
              </tr>
            </thead>
            <tbody>
              {transactions.map((tx) => {
                const source = categorySource(tx.category_source);
                const amount = Number(tx.amount);
                return (
                  <tr key={tx.id}>
                    <td className="data-table__primary">
                      <strong>{tx.merchant ?? tx.description}</strong>
                      {(tx.anomaly_score ?? 0) >= ANOMALY_REVIEW_THRESHOLD && (
                        <Badge variant="warning" description="Unusual for this account">Review</Badge>
                      )}
                    </td>
                    <td>{token && onChanged && onUnauthorized ? <CategoryEditor token={token} id={tx.id} category={tx.category} categories={categories} onChanged={onChanged} onUnauthorized={onUnauthorized} /> : tx.category ?? "Categorizing…"}</td>
                    <td className="data-table__col--source">
                      <Badge description={source.description}>{source.label}</Badge>
                    </td>
                    <td className="data-table__col--date">
                      <time dateTime={tx.occurred_at}>{formatDay(tx.occurred_at)}</time>
                    </td>
                    <td className={`data-table__col--amount ${amount < 0 ? "is-negative" : "is-positive"}`}>
                      {formatMoney(tx.amount, tx.currency)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
