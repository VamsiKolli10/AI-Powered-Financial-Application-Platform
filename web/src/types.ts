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

export type CategoryAmount = {
  category: string;
  amount: string;
  transaction_count: number;
};

export type Summary = {
  period: string;
  total_spend: string;
  by_category: CategoryAmount[];
  summary: string;
  summary_source: string;
  change_vs_previous_pct: number | null;
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
};
