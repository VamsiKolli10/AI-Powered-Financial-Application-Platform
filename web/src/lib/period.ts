export type Period = "monthly" | "weekly";

export const PERIOD_LABEL: Record<Period, { current: string; previous: string }> = {
  monthly: { current: "This month", previous: "last month" },
  weekly: { current: "This week", previous: "last week" },
};

/** Start of the current reporting period in UTC — matches services/insights/periods.py. */
export function periodStart(period: Period, now = new Date()): Date {
  if (period === "monthly") return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
  const midnight = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  const daysSinceMonday = (now.getUTCDay() + 6) % 7;
  return new Date(midnight - daysSinceMonday * 86_400_000);
}
