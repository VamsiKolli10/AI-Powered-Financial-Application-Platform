import type { ReactNode } from "react";
import { cx } from "../lib/cx";

type PanelProps = {
  children: ReactNode;
  id?: string;
  as?: "section" | "article";
  variant?: "default" | "accent";
  /** Content is being refreshed; dims it and sets aria-busy. */
  busy?: boolean;
  className?: string;
  labelledBy?: string;
};

export function Panel({ children, id, as: Tag = "section", variant = "default", busy, className, labelledBy }: PanelProps) {
  return (
    <Tag
      id={id}
      className={cx("panel", variant === "accent" && "panel--accent", busy && "is-busy", className)}
      aria-busy={busy || undefined}
      aria-labelledby={labelledBy}
    >
      {children}
    </Tag>
  );
}

type PanelHeaderProps = { eyebrow: string; title: string; titleId?: string; aside?: ReactNode };

export function PanelHeader({ eyebrow, title, titleId, aside }: PanelHeaderProps) {
  return (
    <div className="panel-header">
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <h2 id={titleId}>{title}</h2>
      </div>
      {aside && <div className="panel-header__aside">{aside}</div>}
    </div>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <p className="empty-state">{children}</p>;
}
