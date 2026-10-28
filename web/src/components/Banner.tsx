import type { ReactNode } from "react";

/** Error message. `inline` sits inside a form or panel; `block` spans the page. */
export function Banner({ children, variant = "block" }: { children: ReactNode; variant?: "block" | "inline" }) {
  return (
    <div className={`banner banner--${variant}`} role="alert">
      {children}
    </div>
  );
}
