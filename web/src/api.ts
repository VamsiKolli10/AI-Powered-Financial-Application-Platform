import { type Period, periodStart } from "./lib/period";
import type {
  Account,
  AssistantStatus,
  ChatResponse,
  Me,
  NotificationList,
  PeriodTransactions,
  PlatformStatus,
  Summary,
  TransactionPage,
  Transaction,
} from "./types";

const API_ROOT = "/api/v1";

export const getAssistantStatus = (token: string) => request<AssistantStatus>("/assistant/status", token);

/** Anomaly score at which the Transactions service flags a row (ANOMALY_ALERT_THRESHOLD). */
export const ANOMALY_REVIEW_THRESHOLD = 0.8;

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init?.headers,
    },
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new ApiError(payload?.error?.message ?? "Request failed", response.status);
  }

  return response.json() as Promise<T>;
}

export async function login(email: string, password: string): Promise<string> {
  const result = await request<{ access_token: string }>("/auth/login", "", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  return result.access_token;
}

export const getMe = (token: string) => request<Me>("/auth/me", token);

export const getAccounts = (token: string) => request<Account[]>("/transactions/accounts", token);
export const getCategories = (token: string) => request<Record<string, string>>("/transactions/categories", token);
export const createTransaction = (token: string, payload: {
  account_id: string; external_tx_id: string; amount: string; currency: string; description: string; occurred_at: string;
}) => request<{ id: string }>("/transactions", token, { method: "POST", body: JSON.stringify(payload) });
export const importTransactions = (token: string, account_id: string, csv_text: string) =>
  request<{ created: number; skipped: number }>("/transactions/import", token, {
    method: "POST", body: JSON.stringify({ account_id, csv_text }),
  });
export const correctCategory = (token: string, id: string, category: string) =>
  request<Transaction>(`/transactions/${encodeURIComponent(id)}`, token, { method: "PATCH", body: JSON.stringify({ category }) });
export const markAlertRead = (token: string, id: string) =>
  request<unknown>(`/notifications/${encodeURIComponent(id)}`, token, { method: "PATCH", body: JSON.stringify({ read: true }) });

export const getRecentTransactions = (token: string, limit = 10) =>
  request<TransactionPage>(`/transactions?limit=${limit}`, token);

const PAGE_SIZE = 100;
const MAX_PAGES = 10;

/** All transactions since the start of the period, following cursors up to a page cap. */
export async function getPeriodTransactions(
  token: string,
  period: Period,
  asOf: Date | string = new Date(),
  start: Date | string = periodStart(period, typeof asOf === "string" ? new Date(asOf) : asOf),
): Promise<PeriodTransactions> {
  // Preserve server microseconds; JavaScript Date would truncate them to milliseconds.
  const from = typeof start === "string" ? start : start.toISOString();
  const to = typeof asOf === "string" ? asOf : asOf.toISOString();
  const items: TransactionPage["items"] = [];
  let cursor: string | null = null;
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const params = new URLSearchParams({ from, to, limit: String(PAGE_SIZE) });
    if (cursor) params.set("cursor", cursor);
    const result: TransactionPage = await request<TransactionPage>(`/transactions?${params}`, token);
    items.push(...result.items);
    cursor = result.next_cursor;
    if (!cursor) return { items, truncated: false };
  }
  return { items, truncated: true };
}

export const getSummary = (token: string, period: Period, asOf?: Date) => {
  const params = new URLSearchParams({ period });
  if (asOf) params.set("as_of", asOf.toISOString());
  return request<Summary>(`/insights/summary?${params}`, token);
};

/** Readiness of every service, probed by the Gateway (cached there for a few seconds). */
export const getStatus = (token: string) => request<PlatformStatus>("/status", token);

export const getNotifications = (token: string) => request<NotificationList>("/notifications?limit=8", token);

export const askAssistant = (token: string, message: string, conversationId?: string) =>
  request<ChatResponse>("/assistant/chat", token, {
    method: "POST",
    body: JSON.stringify({ message, conversation_id: conversationId }),
  });
