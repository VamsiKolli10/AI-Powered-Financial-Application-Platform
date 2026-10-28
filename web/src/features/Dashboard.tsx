import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  getMe,
  getNotifications,
  getPeriodTransactions,
  getRecentTransactions,
  getStatus,
  getSummary,
} from "../api";
import { Banner, Button, SegmentedControl } from "../components";
import { dominantCurrency, greeting } from "../lib/format";
import { PERIOD_LABEL, type Period } from "../lib/period";
import type { Me, NotificationList, PeriodTransactions, Summary, Transaction } from "../types";
import { AlertsPanel } from "./AlertsPanel";
import { AssistantPanel } from "./AssistantPanel";
import {
  fromPlatformStatus,
  SERVICE_LABEL,
  SERVICE_NAMES,
  type ServiceHealth,
  type ServiceState,
  type StatusReport,
} from "./health";
import { MetricsRow } from "./MetricsRow";
import { Sidebar } from "./Sidebar";
import { SpendingPanel } from "./SpendingPanel";
import { TransactionsTable } from "./TransactionsTable";
import { TransactionEntry } from "./TransactionEntry";

const PERIOD_OPTIONS: { value: Period; label: string }[] = [
  { value: "monthly", label: "Month" },
  { value: "weekly", label: "Week" },
];

/** How often the status card re-checks readiness while the tab is visible. */
export const STATUS_REFRESH_MS = 60_000;

function isUnauthorized(result: PromiseSettledResult<unknown>): boolean {
  return result.status === "rejected" && result.reason instanceof ApiError && result.reason.status === 401;
}

function valueOf<T>(result: PromiseSettledResult<T>): T | null {
  return result.status === "fulfilled" ? result.value : null;
}

const state = (result: PromiseSettledResult<unknown>): ServiceState => (result.status === "fulfilled" ? "ok" : "error");

export function Dashboard({ token, onLogout }: { token: string; onLogout: () => void }) {
  const [period, setPeriod] = useState<Period>("monthly");
  const [loadedPeriod, setLoadedPeriod] = useState<Period>("monthly");
  const [me, setMe] = useState<Me | null>(null);
  const [recent, setRecent] = useState<Transaction[] | null>(null);
  const [notifications, setNotifications] = useState<NotificationList | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [periodTransactions, setPeriodTransactions] = useState<PeriodTransactions | null>(null);
  // What this page's own requests found; drives the "some data could not be loaded" banner
  // and stands in for the status card if the Gateway's status endpoint is unreachable.
  const [requestHealth, setRequestHealth] = useState<ServiceHealth>({});
  const [transactionsHealth, setTransactionsHealth] = useState<{ recent?: boolean; period?: boolean }>({});
  const [probe, setProbe] = useState<StatusReport | null | "failed">(null);
  const [baseLoaded, setBaseLoaded] = useState(false);
  const [periodLoaded, setPeriodLoaded] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  // Enrichment and alerts arrive asynchronously. Follow new writes for a minute.
  const [followUntil, setFollowUntil] = useState(0);
  const changed = useCallback(() => { refresh(); setFollowUntil(Date.now() + 60_000); }, [refresh]);
  useEffect(() => {
    if (followUntil <= Date.now()) return;
    const timer = window.setInterval(() => {
      if (Date.now() >= followUntil) { window.clearInterval(timer); return; }
      if (document.visibilityState !== "hidden") refresh();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [followUntil, refresh]);

  // Data that does not depend on the reporting period. One failing service never
  // blanks the page: each section renders its own unavailable state instead.
  useEffect(() => {
    let cancelled = false;
    Promise.allSettled([getMe(token), getRecentTransactions(token), getNotifications(token)]).then(
      ([meResult, recentResult, notificationsResult]) => {
        if (cancelled) return;
        if ([meResult, recentResult, notificationsResult].some(isUnauthorized)) return onLogout();
        setMe(valueOf(meResult));
        setRecent(valueOf(recentResult)?.items ?? null);
        setNotifications(valueOf(notificationsResult));
        setRequestHealth((previous) => ({
          ...previous,
          gateway: state(meResult),
          notifications: state(notificationsResult),
        }));
        setTransactionsHealth((previous) => ({ ...previous, recent: recentResult.status === "fulfilled" }));
        setBaseLoaded(true);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [token, onLogout, revision]);

  // Data scoped to the selected period; refetched when the period changes.
  useEffect(() => {
    let cancelled = false;
    setRefreshing(true);
    const asOf = new Date();
    const summaryRequest = getSummary(token, period);
    const transactionsRequest = summaryRequest.then(
      (result) => getPeriodTransactions(token, period, result.date_to, result.date_from),
      (reason) => {
        if (reason instanceof ApiError && reason.status === 401) throw reason;
        return getPeriodTransactions(token, period, asOf);
      },
    );
    Promise.allSettled([summaryRequest, transactionsRequest]).then(
      ([summaryResult, transactionsResult]) => {
        if (cancelled) return;
        if ([summaryResult, transactionsResult].some(isUnauthorized)) return onLogout();
        setSummary(valueOf(summaryResult));
        setPeriodTransactions(valueOf(transactionsResult));
        setLoadedPeriod(period);
        setRequestHealth((previous) => ({ ...previous, insights: state(summaryResult) }));
        setTransactionsHealth((previous) => ({ ...previous, period: transactionsResult.status === "fulfilled" }));
        setPeriodLoaded(true);
        setRefreshing(false);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [token, period, onLogout, revision]);

  // Service readiness from the Gateway, refreshed while the tab is visible.
  useEffect(() => {
    let cancelled = false;
    const check = () => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      getStatus(token)
        .then((status) => !cancelled && setProbe(fromPlatformStatus(status)))
        .catch((reason) => {
          if (cancelled) return;
          if (reason instanceof ApiError && reason.status === 401) onLogout();
          else setProbe("failed");
        });
    };
    check();
    const timer = window.setInterval(check, STATUS_REFRESH_MS);
    document.addEventListener("visibilitychange", check);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", check);
    };
  }, [token, onLogout]);

  const pageHealth: ServiceHealth = useMemo(() => {
    const { recent: recentOk, period: periodOk } = transactionsHealth;
    const transactions =
      recentOk === undefined || periodOk === undefined ? undefined : recentOk && periodOk ? "ok" : "error";
    return { ...requestHealth, transactions };
  }, [requestHealth, transactionsHealth]);

  const statusReport: StatusReport | null =
    probe === "failed" ? { source: "requests", services: pageHealth } : probe;

  // The summary says which currency its totals are in; everything else follows it.
  const currency = useMemo(
    () => summary?.currency ?? dominantCurrency([...(periodTransactions?.items ?? []), ...(recent ?? [])]),
    [summary, periodTransactions, recent],
  );

  if (!baseLoaded || !periodLoaded) {
    return (
      <main className="loading" role="status" aria-live="polite">
        <div className="loading__pulse" aria-hidden="true" />
        Loading your financial picture…
      </main>
    );
  }

  const unavailable = SERVICE_NAMES.filter((name) => pageHealth[name] === "error").map((name) => SERVICE_LABEL[name]);

  return (
    <div className="app-shell">
      <Sidebar status={statusReport} onLogout={onLogout} />
      <main className="dashboard">
        <header className="dashboard__header">
          <div>
            <span className="eyebrow">Personal finance workspace</span>
            <h1>{greeting(new Date(), me?.full_name)}</h1>
          </div>
          <SegmentedControl label="Reporting period" options={PERIOD_OPTIONS} value={period} onChange={setPeriod} />
        </header>

        {unavailable.length > 0 && (
          <Banner>
            Some data could not be loaded: {unavailable.join(", ")}. The rest of the dashboard is up to date.
          </Banner>
        )}

        {refreshing && <p role="status">Updating {PERIOD_LABEL[period].current.toLowerCase()}…</p>}

        <MetricsRow
          period={loadedPeriod}
          currency={currency}
          periodTransactions={periodTransactions}
          summary={summary}
          notifications={notifications}
        />

        <TransactionEntry token={token} onChanged={changed} onUnauthorized={onLogout} />

        <div className="dashboard__grid">
          <SpendingPanel summary={summary} period={loadedPeriod} currency={currency} busy={refreshing} />
          <AssistantPanel token={token} onUnauthorized={onLogout} />
        </div>

        <Button variant="ghost" onClick={refresh} disabled={refreshing}>Refresh data</Button>
        <TransactionsTable transactions={recent} token={token} onChanged={changed} onUnauthorized={onLogout} />
        <AlertsPanel notifications={notifications} token={token} onChanged={refresh} onUnauthorized={onLogout} />
      </main>
    </div>
  );
}
