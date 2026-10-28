/** Human labels for codes the API returns. Keep wording here, not in components. */

type Label = { label: string; description: string };

const CATEGORY_SOURCES: Record<string, Label> = {
  llm: { label: "AI", description: "Categorized by the AI model" },
  rules: { label: "Rules", description: "Categorized by the deterministic rules engine" },
  user: { label: "Edited", description: "Category set by you" },
};

const SUMMARY_SOURCES: Record<string, Label> = {
  llm: { label: "AI summary", description: "Written by the AI model from your totals" },
  cache: { label: "AI summary", description: "AI summary reused because the totals have not changed" },
  fallback: { label: "Template", description: "Written from a template; the AI model is off or unavailable" },
};

const REPLY_SOURCES: Record<string, Label> = {
  llm: { label: "Worded by AI", description: "Figures computed from your ledger, wording by the AI model" },
  draft: { label: "Direct answer", description: "Figures and wording computed directly from your ledger" },
};

const ALERT_TITLES: Record<string, string> = {
  anomaly_flagged: "Unusual transaction",
  large_transaction: "Large transaction",
  budget_threshold: "Budget threshold reached",
};

const PENDING: Label = { label: "Pending", description: "Waiting to be categorized" };

function lookup(table: Record<string, Label>, code: string | null | undefined): Label {
  if (!code) return PENDING;
  return table[code] ?? { label: code.replaceAll("_", " "), description: code };
}

export const categorySource = (code: string | null | undefined) => lookup(CATEGORY_SOURCES, code);
export const summarySource = (code: string | null | undefined) => lookup(SUMMARY_SOURCES, code);
export const replySource = (code: string | null | undefined) => lookup(REPLY_SOURCES, code);
export const alertTitle = (type: string) => ALERT_TITLES[type] ?? type.replaceAll("_", " ");
