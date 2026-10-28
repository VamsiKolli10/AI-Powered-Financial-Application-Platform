import type { NotificationList, Summary, Transaction } from "../types";
import { periodStart } from "../lib/period";

export function tx(overrides: Partial<Transaction> = {}): Transaction {
  return {
    id: "tx_1",
    account_id: "acct_1",
    amount: "-10.00",
    currency: "USD",
    description: "MERCHANT",
    merchant: "Merchant",
    category: "Shopping",
    category_source: "rules",
    anomaly_score: 0.05,
    occurred_at: "2026-10-01T12:00:00Z",
    ...overrides,
  };
}

export function summary(overrides: Partial<Summary> = {}): Summary {
  return {
    period: "2026-10",
    date_from: periodStart("monthly").toISOString(),
    date_to: new Date().toISOString(),
    currency: "USD",
    total_spend: "1700.00",
    by_category: [
      { category: "Housing", amount: "1500.00", transaction_count: 1 },
      { category: "Groceries", amount: "200.00", transaction_count: 3 },
    ],
    summary: "October so far is mostly rent.",
    summary_source: "fallback",
    change_vs_previous_pct: -12.4,
    other_currencies: [],
    ...overrides,
  };
}

export const noAlerts: NotificationList = { notifications: [], unread_count: 0 };

type Route = { status?: number; body: unknown } | ((url: URL) => { status?: number; body: unknown });

/** A fetch stand-in keyed by API path ("/transactions", "/status", ...). */
export function mockApi(routes: Record<string, Route>) {
  return async (input: RequestInfo | URL): Promise<Response> => {
    const url = new URL(String(input), "http://localhost");
    const path = url.pathname.replace(/^\/api\/v1/, "");
    const route = routes[path];
    if (!route) {
      return new Response(JSON.stringify({ error: { code: "NOT_FOUND", message: `No mock for ${path}` } }), {
        status: 404,
      });
    }
    const { status = 200, body } = typeof route === "function" ? route(url) : route;
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  };
}
