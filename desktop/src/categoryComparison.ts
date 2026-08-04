/** Whether a category breakdown is allowed to say "higher" or "lower".
 *
 * Pulled out of CategoryBars so it can be tested directly: charts.tsx is JSX
 * and the test runner only strips types.
 *
 * The bug this exists to prevent: the component decided it had a baseline
 * from `previousMonth`, which is only the month's *name* and is always
 * populated. When the previous month had not been imported far enough to
 * compare against, the engine sent previous=null on every item, and the bars
 * still rendered "▲ $84.20 higher than the same point last month" against a
 * month that had no data in it at all.
 */
export interface ComparableItem {
  amount: number;
  share: number;
  previous: number | null;
  delta: number | null;
  average?: number | null;
  average_delta?: number | null;
}

export type Baseline = "last" | "average";

/** Is there a real baseline behind these bars? */
export function canCompare(
  items: ComparableItem[],
  baseline: Baseline,
  comparisonAvailable: boolean,
  previousMonth: string,
): boolean {
  if (baseline === "average") return items.some((i) => i.average != null);
  return comparisonAvailable && !!previousMonth;
}

/** The baseline value to draw a marker at, or null for no marker. */
export function baselineOf(
  item: ComparableItem, baseline: Baseline,
): number | null {
  return baseline === "average" ? item.average ?? null : item.previous;
}

/** The difference to describe, or null when there is nothing to describe. */
export function differenceOf(
  item: ComparableItem, baseline: Baseline, comparable: boolean,
): number | null {
  if (!comparable) return null;
  return baseline === "average" ? item.average_delta ?? null : item.delta;
}

/** The sentence under each bar. Share only whenever there is no baseline.
 *  Takes the formatter so the component and this module cannot drift. */
export function categoryCaption(
  item: ComparableItem,
  difference: number | null,
  referenceLabel: string,
  formatMoney: (value: number) => string,
): string {
  if (difference == null) return `${item.share.toFixed(0)}% of spending`;
  if (Math.abs(difference) < 1) return `roughly in line with ${referenceLabel}`;
  const money = formatMoney(Math.abs(difference));
  return difference > 0
    ? `▲ ${money} higher than ${referenceLabel}`
    : `▼ ${money} below ${referenceLabel}`;
}
