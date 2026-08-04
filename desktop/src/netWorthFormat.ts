/** Wording for a period the user cannot be shown a number for, and for a
 *  span measured in calendar months rather than in recorded rows.
 *
 *  Split out of InsightsView so it can be tested: the file is JSX and the
 *  test runner only strips types.
 */

/** "6 months", "1 month", "2 years and 1 month". Never a row count. */
export function describeSpan(months: number): string {
  const total = Math.max(0, Math.round(months));
  if (total === 0) return "the same month";
  if (total < 12) return `${total} month${total === 1 ? "" : "s"}`;
  const years = Math.floor(total / 12);
  const rest = total % 12;
  const yearPart = `${years} year${years === 1 ? "" : "s"}`;
  if (rest === 0) return yearPart;
  return `${yearPart} and ${rest} month${rest === 1 ? "" : "s"}`;
}

/** A signed money string, or null when there is nothing to sign.
 *
 *  Returning null rather than "+$0.00" is the whole point: an absent
 *  comparison month is not a month in which nothing happened.
 */
export function signedAmount(
  amount: number | null | undefined,
  formatMoney: (value: number) => string,
): string | null {
  if (amount == null) return null;
  return `${amount >= 0 ? "+" : "\u2212"}${formatMoney(Math.abs(amount))}`;
}
