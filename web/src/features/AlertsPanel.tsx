import { useState } from "react";
import { ApiError, markAlertRead } from "../api";
import { Badge, Button, EmptyState, Panel, PanelHeader, StatusDot } from "../components";
import { cx } from "../lib/cx";
import { formatDay } from "../lib/format";
import { alertTitle } from "../lib/labels";
import type { NotificationList } from "../types";

export function AlertsPanel({ notifications, token, onChanged, onUnauthorized }: {
  notifications: NotificationList | null; token?: string; onChanged?: () => void; onUnauthorized?: () => void;
}) {
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState("");
  async function mark(id: string) {
    if (!token) return;
    setPending(id); setError("");
    try { await markAlertRead(token, id); onChanged?.(); }
    catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) onUnauthorized?.();
      else setError("Alert could not be marked as read. Try again.");
    } finally { setPending(null); }
  }
  return (
    <Panel id="alerts" className="alerts" labelledBy="alerts-title">
      <PanelHeader eyebrow="Monitoring" title="Recent alerts" titleId="alerts-title" />
      {error && <p role="alert">{error}</p>}
      {!notifications ? (
        <EmptyState>Alerts are unavailable right now.</EmptyState>
      ) : notifications.notifications.length === 0 ? (
        <EmptyState>No recent alerts. That is usually good news.</EmptyState>
      ) : (
        <ul className="alerts__list">
          {notifications.notifications.map((item) => (
            <li key={item.id} className={cx("alert-row", item.read && "alert-row--read")}>
              <StatusDot tone={item.read ? "muted" : "alert"} />
              <div>
                <strong>
                  {alertTitle(item.type)}
                  {!item.read && <Badge variant="alert" className="alert-row__new">New</Badge>}
                </strong>
                <p>{item.message}</p>
              </div>
              <time dateTime={item.created_at}>{formatDay(item.created_at)}</time>
              {!item.read && token && <Button variant="ghost" disabled={pending !== null} loading={pending === item.id} onClick={() => void mark(item.id)}>Mark as read</Button>}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
