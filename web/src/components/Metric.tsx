import { cx } from "../lib/cx";

type MetricProps = {
  label: string;
  /** Pre-formatted value; `null` renders an em dash for unavailable data. */
  value: string | null;
  caption: string;
  tone?: "default" | "alert";
};

export function Metric({ label, value, caption, tone = "default" }: MetricProps) {
  return (
    <article className={cx("metric", tone === "alert" && "metric--alert")}>
      <span className="metric__label">{label}</span>
      <strong className="metric__value">
        {value ?? (
          <>
            <span aria-hidden="true">—</span>
            <span className="sr-only">Unavailable</span>
          </>
        )}
      </strong>
      <small className="metric__caption">{caption}</small>
    </article>
  );
}
