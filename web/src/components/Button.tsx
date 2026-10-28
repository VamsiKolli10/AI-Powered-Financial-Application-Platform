import type { ButtonHTMLAttributes } from "react";
import { cx } from "../lib/cx";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost";
  loading?: boolean;
  /** Shown instead of the children while `loading`. */
  loadingLabel?: string;
};

export function Button({
  variant = "primary",
  loading = false,
  loadingLabel,
  className,
  disabled,
  children,
  type = "button",
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cx("button", `button--${variant}`, className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading && loadingLabel ? loadingLabel : children}
    </button>
  );
}
