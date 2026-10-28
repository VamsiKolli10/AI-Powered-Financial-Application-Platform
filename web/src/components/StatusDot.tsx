export function StatusDot({ tone }: { tone: "ok" | "alert" | "muted" }) {
  return <span className={`dot dot--${tone}`} aria-hidden="true" />;
}
