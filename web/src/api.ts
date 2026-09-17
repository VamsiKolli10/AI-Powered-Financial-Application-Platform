import type { ChatResponse, NotificationList, Summary, TransactionPage } from "./types";

const API_ROOT = "/api/v1";

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
    throw new ApiError(payload?.detail?.message ?? payload?.detail ?? "Request failed", response.status);
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

export const getTransactions = (token: string) =>
  request<TransactionPage>("/transactions?limit=50", token);

export const getSummary = (token: string) =>
  request<Summary>("/insights/summary?period=monthly", token);

export const getNotifications = (token: string) =>
  request<NotificationList>("/notifications?limit=8", token);

export const askAssistant = (token: string, message: string, conversationId?: string) =>
  request<ChatResponse>("/assistant/chat", token, {
    method: "POST",
    body: JSON.stringify({ message, conversation_id: conversationId }),
  });
