/** Labels for a rolling comparison must disclose the months actually used. */
export function rollingAverageLabel(
  monthCount: number | null | undefined,
  compact = false,
): string {
  const count = Math.max(0, Math.trunc(Number(monthCount) || 0));
  if (count === 0) return compact ? "Recent avg" : "Recent same-day average";
  return compact ? `${count}-mo avg` : `${count}-month same-day average`;
}

/** Pick real extrema. In an all-negative run, "best" is still negative. */
export function cashflowExtremes<T extends { net: number }>(months: T[]): {
  best: T;
  weakest: T;
} {
  if (!months.length) throw new Error("Cash-flow extrema need at least one month.");
  return {
    best: months.reduce((a, b) => (b.net > a.net ? b : a)),
    weakest: months.reduce((a, b) => (b.net < a.net ? b : a)),
  };
}
