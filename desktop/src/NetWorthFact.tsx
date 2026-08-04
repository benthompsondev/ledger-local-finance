import { money } from "./money";
import { describeSpan, signedAmount } from "./netWorthFormat";
import type { NetWorthMove } from "./types";

/**
 * One figure in the Net worth summary row.
 *
 * The point of this component is the `move == null` branch. The version it
 * replaces rendered `trend.month_over_month?.amount ?? 0`, so a period with
 * no reading to compare against displayed a confident **+$0.00** — a claim
 * that net worth held steady, made about months that were never recorded.
 * An absent comparison says so instead.
 */
export function NetWorthFact({
  label, move, missing,
}: {
  label: string;
  move: NetWorthMove | null;
  /** Shown instead of a figure when the comparison month has no reading. */
  missing: string;
}) {
  if (move == null) {
    return (
      <div className="nw-fact-missing">
        <span>{label}</span>
        <strong>Not yet</strong>
        <small>{missing}</small>
      </div>
    );
  }
  return (
    <div>
      <span>{label}</span>
      <strong className={move.amount >= 0 ? "good-text" : ""}>
        {signedAmount(move.amount, money)}
      </strong>
      <small>
        {move.from_month} to {move.to_month}
        {move.months_apart !== 1 && ` · ${describeSpan(move.months_apart)}`}
      </small>
    </div>
  );
}
