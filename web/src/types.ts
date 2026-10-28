export type Me = {
  id: string;
  email: string;
  full_name: string | null;
};

export type Account = { id: string; name: string; currency: string };

export type Transaction = {
  id: string;
  account_id: string;
  amount: string;
  currency: string;
  description: string;
  merchant: string | null;
  category: string | null;
  category_source: string | null;
  anomaly_score: number | null;
  occurred_at: string;
};

export type TransactionPage = {
  items: Transaction[];
  next_cursor: string | null;
  limit: number;
};

/** Every transaction in a reporting period, gathered across pages. */
export type PeriodTransactions = {
  items: Transaction[];
  /** True when the page cap was hit and older transactions in the period were not loaded. */
  truncated: boolean;
};

export type CategoryAmount = {
  category: string;
  amount: string;
  transaction_count: number;
};

export type Summary = {
  period: string;
  date_from: string;
  date_to: string;
  /** ISO 4217 code every amount in the summary is in. */
  currency: string;
  total_spend: string;
  by_category: CategoryAmount[];
  summary: string;
  summary_source: string;
  change_vs_previous_pct: number | null;
  /** Currencies with spending in the period that the totals leave out. */
  other_currencies: string[];
};

export type PlatformStatus = {
  status: "ok" | "degraded";
  services: Record<string, "ok" | "unavailable">;
  checked_at: string;
};

export type Notification = {
  id: string;
  type: string;
  message: string;
  created_at: string;
  read: boolean;
};

export type NotificationList = {
  notifications: Notification[];
  unread_count: number;
};

export type ChatResponse = {
  conversation_id: string;
  reply: string;
  reply_source: string;
  fallback_reason?: string | null;
};

export type AssistantStatus = {
  mode: "llm" | "disabled" | "unconfigured";
  model: string | null;
  message: string;
};
