import type { ReactNode } from "react";
import { cx } from "../lib/cx";

type BadgeProps = {
  children: ReactNode;
  variant?: "neutral" | "warning" | "alert";
  /** Longer explanation, shown as a tooltip and read by screen readers. */
  description?: string;
  className?: string;
};

export function Badge({ children, variant = "neutral", description, className }: BadgeProps) {
  return (
    <span className={cx("badge", `badge--${variant}`, className)} title={description}>
      {children}
      {description && <span className="sr-only">: {description}</span>}
    </span>
  );
}
