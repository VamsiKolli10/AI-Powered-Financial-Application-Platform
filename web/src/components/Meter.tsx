/** Decorative bar: the value it represents is always printed next to it. */
export function Meter({ value, max }: { value: number; max: number }) {
  const width = max > 0 ? Math.max(5, (value / max) * 100) : 0;
  return (
    <div className="meter" aria-hidden="true">
      <i style={{ width: `${Math.min(width, 100)}%` }} />
    </div>
  );
}
