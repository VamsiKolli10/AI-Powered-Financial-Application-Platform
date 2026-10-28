import type { PlatformStatus } from "../types";

export const SERVICE_NAMES = ["gateway", "transactions", "assistant", "insights", "notifications"] as const;
export type ServiceName = (typeof SERVICE_NAMES)[number];
export type ServiceState = "ok" | "error";
export type ServiceHealth = Partial<Record<ServiceName, ServiceState>>;

/**
 * What the status card shows. `probe` comes from the Gateway asking each service's
 * readiness endpoint; `requests` is the fallback when that endpoint cannot be reached,
 * built from whether this page's own data requests succeeded.
 */
export type StatusReport = { source: "probe" | "requests"; services: ServiceHealth };

export const SERVICE_LABEL: Record<ServiceName, string> = {
  gateway: "Account",
  transactions: "Transactions",
  assistant: "Assistant",
  insights: "Insights",
  notifications: "Alerts",
};

export function fromPlatformStatus(status: PlatformStatus): StatusReport {
  const services: ServiceHealth = {};
  for (const name of SERVICE_NAMES) {
    const state = status.services[name];
    if (state) services[name] = state === "ok" ? "ok" : "error";
  }
  return { source: "probe", services };
}
