/**
 * Bespoke SVG/HTML charts for the native app. No chart library: the app is
 * offline-only with a strict CSP, the datasets are small and chart-ready
 * (the Python engine computes all financial truth), and hand-rolled marks
 * keep the bundle tiny.
 *
 * Color system (validated against the dark surface with the dataviz palette
 * checks — lightness band, chroma, CVD separation, contrast):
 *   income  #22C55E (green)   spending #8B5CF6 (violet)
 *   accent  #1E9FFF (blue, current-month pace / magnitude bars)
 *   previous-period reference lines use muted ink, not a series color.
 * Identity is never color-alone: every series is directly labeled or
 * legended, and deltas always carry a ▲/▼ symbol and signed text.
 *
 * These are the SignalSpace tokens from styles.css, repeated as literals
 * because SVG presentation attributes do not resolve var(). The roles were
 * already right — green income, violet spending, blue structure — so this is
 * a retune to the brand values, not a re-assignment. Violet was brightened
 * rather than pushed toward the mockup's bluer #6C4DFF, which would have
 * narrowed the hue gap against the blue accent and cost the CVD separation
 * the original system was validated for.
 */
import { useState, type MouseEvent } from "react";
import {
  baselineOf, canCompare, categoryCaption, differenceOf,
} from "./categoryComparison";
import { cashflowExtremes, rollingAverageLabel } from "./insightComparison";
import { money, moneyCents } from "./money";
import { signedAmount } from "./netWorthFormat";
import type {
  CalendarDay, CashflowMonth, CategoryPaceItem, DayOfWeekRow, IncomeSourceItem,
  NetWorthOverview, PacePoint, SpendingPace, SpendingPatterns,
} from "./types";
import { niceMax } from "./chartScale";
import { readWeekStart } from "./preferences";

const AVG_COLOR = "#E3B341";

export const VIZ = {
  income: "#22C55E",
  spending: "#8B5CF6",
  accent: "#1E9FFF",
  previous: "#8B99AA",
  grid: "#253449",
  ink: "#8B99AA",
};

function shortMonth(month: string): string {
  const [y, m] = month.split("-").map(Number);
  return new Date(y, (m || 1) - 1, 1).toLocaleString("en-CA", {
    month: "short",
  });
}

/** Grouped income/spending bars per month. Partial months render at reduced
 * opacity with a "partial" tag so they are never read as complete. */
export function CashflowChart({ months, onDrill }: { months: CashflowMonth[]; onDrill?: (month:string, role:"income"|"spending")=>void }) {
  if (months.length === 0) {
    return (
      <p className="guidance">
        Import at least one month of statements to see the cash-flow trend.
      </p>
    );
  }
  const shown = months.slice(-12);
  // The viewBox is sized to the number of months so three months fill the
  // card instead of huddling on the left of a twelve-month canvas. Bars are
  // then a share of their slot rather than a fixed cap, which stops a short
  // range rendering as pinstripes in a field of empty space.
  const W = Math.max(340, 96 + shown.length * 58);
  const H = 250;
  const padL = 46;
  const padB = 32;
  const padT = 14;
  const plotW = W - padL - 8;
  const plotH = H - padT - padB;
  const max = niceMax(
    Math.max(...shown.map((m) => Math.max(m.income, m.spending)), 1),
  );
  const group = plotW / shown.length;
  const barW = Math.max(9, group * 0.36);
  const y = (v: number) => padT + plotH - (v / max) * plotH;
  const gridLines = [0.5, 1].map((f) => max * f);
  const latestComplete = [...shown].reverse().find((m) => m.complete);

  return (
    <div>
      <div className="viz-legend">
        <span>
          <i style={{ background: VIZ.income }} /> Income
        </span>
        <span>
          <i style={{ background: VIZ.spending }} /> Spending
        </span>
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`Monthly income and spending for the last ${shown.length} months`}
        style={{ width: "100%", height: "auto", display: "block" }}
      >
        {gridLines.map((v) => (
          <g key={v}>
            <line
              x1={padL}
              x2={W - 8}
              y1={y(v)}
              y2={y(v)}
              stroke={VIZ.grid}
              strokeWidth="1"
            />
            <text x={padL - 6} y={y(v) + 4} textAnchor="end" fontSize="10" fill={VIZ.ink}>
              {money(v)}
            </text>
          </g>
        ))}
        <line
          x1={padL}
          x2={W - 8}
          y1={y(0)}
          y2={y(0)}
          stroke={VIZ.ink}
          strokeWidth="1"
        />
        {shown.map((m, i) => {
          const cx = padL + group * i + group / 2;
          const opacity = m.complete ? 1 : 0.55;
          return (
            <g key={m.month} opacity={opacity}>
              <rect
                x={cx - barW - 1}
                width={barW}
                y={y(m.income)}
                height={Math.max(0, y(0) - y(m.income))}
                rx="2"
                fill={VIZ.income}
                className="chart-bar-click"
                tabIndex={0}
                role="button"
                aria-label={`Open ${m.month} income transactions`}
                onClick={()=>onDrill?.(m.month,"income")}
                onKeyDown={event=>{if(event.key==="Enter"||event.key===" ")onDrill?.(m.month,"income");}}
              >
                <title>
                  {`${m.month}${m.complete ? "" : " (partial)"} — income ${moneyCents(m.income)}, spending ${moneyCents(m.spending)}, kept ${moneyCents(m.net)}`}
                </title>
              </rect>
              <rect
                x={cx + 1}
                width={barW}
                y={y(m.spending)}
                height={Math.max(0, y(0) - y(m.spending))}
                rx="2"
                fill={VIZ.spending}
                className="chart-bar-click"
                tabIndex={0}
                role="button"
                aria-label={`Open ${m.month} spending transactions`}
                onClick={()=>onDrill?.(m.month,"spending")}
                onKeyDown={event=>{if(event.key==="Enter"||event.key===" ")onDrill?.(m.month,"spending");}}
              >
                <title>
                  {`${m.month}${m.complete ? "" : " (partial)"} — income ${moneyCents(m.income)}, spending ${moneyCents(m.spending)}, kept ${moneyCents(m.net)}`}
                </title>
              </rect>
              <text
                x={cx}
                y={H - 8}
                textAnchor="middle"
                fontSize="10"
                fill={VIZ.ink}
              >
                {shortMonth(m.month)}
                {m.complete ? "" : "*"}
              </text>
            </g>
          );
        })}
      </svg>
      <CashflowReading months={shown} latestComplete={latestComplete} />
    </div>
  );
}

function median(values: number[]): number {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

/**
 * What the bars actually say, in words.
 *
 * A single line naming the last complete month described one bar out of
 * twelve. The interesting content of this chart is the comparison: whether
 * this month is normal for you, whether income holds steady, and whether the
 * recent run is better or worse than the run before it. All of it comes from
 * the same months already drawn, so nothing here can disagree with the chart.
 *
 * Only complete months are read. A direction is claimed only with four of
 * them, and only when the two halves differ by enough to be a real change
 * rather than the ordinary wobble of a month.
 */
function CashflowReading({ months, latestComplete }: {
  months: CashflowMonth[];
  latestComplete?: CashflowMonth;
}) {
  const complete = months.filter((m) => m.complete);
  const partial = months.some((m) => !m.complete);
  if (!complete.length) {
    return (
      <p className="file-meta">
        No month in this range has finished yet, so there is nothing to
        compare against.
      </p>
    );
  }

  const kept = complete.map((m) => m.net);
  const typicalKept = median(kept);
  const typicalRate = median(complete.map((m) => m.savings_rate));
  const { best, weakest } = cashflowExtremes(complete);
  const incomes = complete.map((m) => m.income);
  const incomeHigh = Math.max(...incomes);
  const incomeLow = Math.min(...incomes);
  const incomeSwing = incomeHigh > 0 ? (incomeHigh - incomeLow) / incomeHigh : 0;

  // Two halves, oldest against newest. With an odd count the middle month
  // belongs to neither, so it cannot be counted twice.
  let direction: { earlier: number; recent: number; delta: number } | null = null;
  if (complete.length >= 4) {
    const half = Math.floor(complete.length / 2);
    const earlier = complete.slice(0, half).reduce((s, m) => s + m.net, 0) / half;
    const recent = complete.slice(-half).reduce((s, m) => s + m.net, 0) / half;
    direction = { earlier, recent, delta: recent - earlier };
  }
  const MATERIAL = 50;

  const vsTypical = latestComplete ? latestComplete.net - typicalKept : 0;

  return (
    <>
      <div className="cf-facts">
        <div>
          <span>Typical month</span>
          <strong className={typicalKept >= 0 ? "good-text" : ""}>
            {typicalKept >= 0 ? "+" : "−"}{money(Math.abs(typicalKept))}
          </strong>
          <small>{typicalRate.toFixed(0)}% of income, across {complete.length} finished month{complete.length === 1 ? "" : "s"}</small>
        </div>
        <div>
          <span>Best</span>
          <strong className={best.net >= 0 ? "good-text" : ""}>
            {signedAmount(best.net, money)}
          </strong>
          <small>{best.month} · {best.savings_rate.toFixed(0)}% kept</small>
        </div>
        <div>
          <span>Weakest</span>
          <strong>{weakest.net >= 0 ? "+" : "−"}{money(Math.abs(weakest.net))}</strong>
          <small>{weakest.month} · {weakest.savings_rate.toFixed(0)}% kept</small>
        </div>
        <div>
          <span>Money in</span>
          <strong>{money(median(incomes))}</strong>
          <small>{incomeSwing >= 0.15
            ? `varies from ${money(incomeLow)} to ${money(incomeHigh)}`
            : "steady month to month"}</small>
        </div>
      </div>
      <p className="file-meta">
        {latestComplete && <>
          {latestComplete.month} finished at {money(latestComplete.net)} kept
          from {money(latestComplete.income)} in.{" "}
          {Math.abs(vsTypical) < MATERIAL
            ? "That is an ordinary month for you."
            : `That is ${money(Math.abs(vsTypical))} ${vsTypical > 0 ? "better" : "worse"} than your typical month.`}{" "}
        </>}
        {direction && (Math.abs(direction.delta) < MATERIAL
          ? "Across this range the months hold roughly level."
          : `The recent half of this range averaged ${money(direction.recent)} a month against ${money(direction.earlier)} before it, ${direction.delta > 0 ? "so the trend is upward" : "so the trend is downward"}.`)}
        {incomeSwing >= 0.15 && " Income moves enough month to month that a single month is a poor guide on its own."}
        {partial && " The partial month is drawn faded."}
      </p>
    </>
  );
}

/** Cumulative spending this month vs the previous month and up-to-3-month baseline,
 * always compared through the SAME day-of-month. The current month is the
 * dominant solid line; comparisons are muted, dashed reference lines. The
 * x-axis spans only the days actually shown, so the lines use the full width
 * instead of being crushed against a full-month scale. */
/**
 * Explainer wording for a month-to-date comparison. Never claims "the same day"
 * when the prior month ended earlier than the current analysis day.
 */
export function paceComparisonNote(pace: {
  current_through_day?: number;
  comparison_through_day?: number;
  through_day?: number;
  previous_month?: string;
  comparison_available?: boolean;
}): string {
  const currentDay = pace.current_through_day ?? pace.through_day ?? 0;
  const comparisonDay = pace.comparison_through_day ?? currentDay;
  // Saying what the amounts are compared with, when they are compared with
  // nothing, is the same invention the bars themselves used to make.
  if (pace.comparison_available === false) {
    return `Amounts through day ${currentDay}. The previous month is not imported far enough to compare against.`;
  }
  if (comparisonDay < currentDay) {
    const prior = pace.previous_month ? shortMonth(pace.previous_month) : "the prior month";
    return `Amounts through day ${currentDay}, compared with all ${comparisonDay} days of ${prior}.`;
  }
  return `Amounts through day ${currentDay}, compared with the prior month through the same day.`;
}

export function PaceChart({
  pace,
  currentLabel = "Current period",
}: {
  pace: SpendingPace;
  currentLabel?: string;
}) {
  const [hoverDay, setHoverDay] = useState<number | null>(null);
  if (!pace.available || pace.current.length === 0) {
    return (
      <p className="guidance">
        Spending pace appears once {currentLabel} has activity.
      </p>
    );
  }
  const hasPrev = pace.previous.length > 0 && pace.previous_total_same_day != null;
  const hasAvg = !!pace.average_90_day?.length && pace.average_90_day_total != null;
  const averageLabel = rollingAverageLabel(pace.average_90_day_month_count);
  const compactAverageLabel = rollingAverageLabel(
    pace.average_90_day_month_count, true,
  );
  // Axis spans the displayed same-day data, never the whole calendar month.
  const maxDay = Math.max(
    pace.day, pace.current.length, pace.previous.length,
    pace.average_90_day?.length ?? 0, 2,
  );
  const W = 680, H = 250, padL = 48, padR = 66, padB = 26, padT = 14;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;
  const maxV = niceMax(Math.max(
    pace.current_total, pace.previous_total_same_day ?? 0,
    pace.average_90_day_total ?? 0, 1,
  ));
  const x = (day: number) => padL + ((day - 1) / Math.max(1, maxDay - 1)) * plotW;
  const y = (v: number) => padT + plotH - (v / maxV) * plotH;
  const line = (pts: PacePoint[]) =>
    pts.map((p, i) => `${i ? "L" : "M"}${x(p.day).toFixed(1)},${y(p.cumulative).toFixed(1)}`).join(" ");
  const at = (pts: PacePoint[], day: number): number | null =>
    pts.length ? (pts[Math.min(day, pts.length) - 1] ?? pts[pts.length - 1]).cumulative : null;

  const vsPrev = hasPrev ? pace.delta : null;
  // Both deltas come from the engine on an equal-span basis. Comparing the full
  // month-to-date total against a shorter prior month would overstate spending.
  const vsAvg = hasAvg ? pace.average_90_day_delta : null;
  const comparisonDay = pace.comparison_through_day ?? pace.day;
  const shortComparison = comparisonDay < (pace.current_through_day ?? pace.day);
  const priorLabel = shortComparison
    ? `All of ${shortMonth(pace.previous_month)} (${comparisonDay} days)`
    : `By day ${comparisonDay} last month`;

  // Direct end-of-line tags, nudged apart so labels never overlap.
  const tags = [
    { key: "cur", label: "This mo", color: VIZ.accent, v: pace.current_total, bold: true },
    ...(hasPrev ? [{ key: "prev", label: shortMonth(pace.previous_month), color: VIZ.previous, v: pace.previous_total_same_day as number, bold: false }] : []),
    ...(hasAvg ? [{ key: "avg", label: compactAverageLabel, color: AVG_COLOR, v: pace.average_90_day_total as number, bold: false }] : []),
  ].map((t) => ({ ...t, ty: y(t.v) })).sort((a, b) => a.ty - b.ty);
  for (let i = 1; i < tags.length; i += 1) {
    if (tags[i].ty - tags[i - 1].ty < 13) tags[i].ty = tags[i - 1].ty + 13;
  }

  const onMove = (e: MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const vbx = ((e.clientX - rect.left) / rect.width) * W;
    const day = Math.round(1 + ((vbx - padL) / plotW) * (maxDay - 1));
    setHoverDay(Math.min(maxDay, Math.max(1, day)));
  };
  const hCur = hoverDay != null ? at(pace.current, hoverDay) : null;
  const hPrev = hoverDay != null && hasPrev ? at(pace.previous, hoverDay) : null;
  const hAvg = hoverDay != null && hasAvg ? at(pace.average_90_day, hoverDay) : null;

  return (
    <div className="pace-card">
      <div className="pace-summary">
        <div className="pace-stat pace-stat-primary">
          <span>Spent so far · day {pace.day}</span>
          <strong>{money(pace.current_total)}</strong>
        </div>
        <div className="pace-stat">
          <span>{priorLabel}</span>
          <strong>{hasPrev ? money(pace.previous_total_same_day as number) : "—"}</strong>
          {vsPrev != null && <em className={vsPrev > 0 ? "pace-up" : "pace-down"}>{vsPrev > 0 ? "▲" : "▼"} {money(Math.abs(vsPrev))}</em>}
        </div>
        <div className="pace-stat">
          <span>{averageLabel}</span>
          <strong>{hasAvg ? money(pace.average_90_day_total as number) : "—"}</strong>
          {vsAvg != null && <em className={vsAvg > 0 ? "pace-up" : "pace-down"}>{vsAvg > 0 ? "▲" : "▼"} {money(Math.abs(vsAvg))}</em>}
        </div>
      </div>
      {!hasAvg && pace.average_90_day_reason && (
        <p className="guidance pace-history-note">{pace.average_90_day_reason}</p>
      )}
      <div className="viz-legend pace-legend">
          <span><i style={{ background: VIZ.accent }} /> {currentLabel}</span>
        {hasPrev && <span><i className="dash" style={{ background: VIZ.previous }} /> {shortMonth(pace.previous_month)}</span>}
        {hasAvg && <span><i className="dot" style={{ background: AVG_COLOR }} /> {averageLabel}</span>}
      </div>
      <div className="pace-plot">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          role="img"
          aria-label={`Cumulative spending through day ${pace.day}: ${moneyCents(pace.current_total)} in ${currentLabel}${hasPrev ? `, ${moneyCents(pace.previous_total_same_day as number)} ${shortComparison ? `across all ${comparisonDay} days of the prior month` : "by the same day in the prior month"}` : ""}`}
          style={{ width: "100%", height: "auto", display: "block" }}
          onMouseMove={onMove}
          onMouseLeave={() => setHoverDay(null)}
        >
          {[0.5, 1].map((f) => (
            <g key={f}>
              <line x1={padL} x2={padL + plotW} y1={y(maxV * f)} y2={y(maxV * f)} stroke={VIZ.grid} strokeWidth="1" />
              <text x={padL - 6} y={y(maxV * f) + 4} textAnchor="end" fontSize="10" fill={VIZ.ink}>{money(maxV * f)}</text>
            </g>
          ))}
          <line x1={padL} x2={padL + plotW} y1={y(0)} y2={y(0)} stroke={VIZ.ink} strokeWidth="1" />
          {[1, Math.max(2, Math.round(maxDay / 2)), maxDay].map((d) => (
            <text key={d} x={x(d)} y={H - 8} textAnchor="middle" fontSize="10" fill={VIZ.ink}>{d}</text>
          ))}
          {hasAvg && <path d={line(pace.average_90_day)} fill="none" stroke={AVG_COLOR} strokeWidth="2" strokeDasharray="2 4" />}
          {hasPrev && <path d={line(pace.previous)} fill="none" stroke={VIZ.previous} strokeWidth="2" strokeDasharray="6 4" />}
          <path d={line(pace.current)} fill="none" stroke={VIZ.accent} strokeWidth="3" />
          {hoverDay != null && (
            <g>
              <line x1={x(hoverDay)} x2={x(hoverDay)} y1={padT} y2={y(0)} stroke={VIZ.ink} strokeWidth="1" strokeDasharray="3 3" opacity="0.6" />
              {hCur != null && <circle cx={x(hoverDay)} cy={y(hCur)} r="3.5" fill={VIZ.accent} />}
              {hPrev != null && <circle cx={x(hoverDay)} cy={y(hPrev)} r="3.5" fill={VIZ.previous} />}
              {hAvg != null && <circle cx={x(hoverDay)} cy={y(hAvg)} r="3.5" fill={AVG_COLOR} />}
            </g>
          )}
          {tags.map((t) => (
            <text key={t.key} x={padL + plotW + 8} y={t.ty + 3} fontSize="10" fontWeight={t.bold ? 700 : 400} fill={t.bold ? "#C2CEDC" : t.color}>
              {t.label}
            </text>
          ))}
        </svg>
        {hoverDay != null && hCur != null && (
          <div className="pace-tooltip" style={{ left: `${(x(hoverDay) / W) * 100}%` }}>
            <strong>Day {hoverDay}</strong>
            <span><i style={{ background: VIZ.accent }} />{currentLabel} {money(hCur)}</span>
            {hPrev != null && <span><i style={{ background: VIZ.previous }} />{shortMonth(pace.previous_month)} {money(hPrev)}</span>}
            {hAvg != null && <span><i style={{ background: AVG_COLOR }} />{compactAverageLabel} {money(hAvg)}</span>}
          </div>
        )}
      </div>
      <p className="file-meta">
        Through day {pace.day} of {pace.days_in_month} for {currentLabel}.{" "}
        {vsPrev != null
          ? vsPrev > 0
            ? `You've spent ${money(Math.abs(vsPrev))} more than by this point in ${pace.previous_month}`
            : `You've spent ${money(Math.abs(vsPrev))} less than by this point in ${pace.previous_month}`
          : `No comparable data for ${pace.previous_month} yet`}
        {vsAvg != null
          ? `, and ${vsAvg > 0 ? `${money(Math.abs(vsAvg))} above` : `${money(Math.abs(vsAvg))} below`} your ${averageLabel}.`
          : "."}
      </p>
    </div>
  );
}

/** Ranked, comparative category rows for the current month through today.
 * Each row is a bullet chart: the accent bar is what you've spent so far, and
 * the marker shows your chosen baseline (last month same day, or your 3-month
 * average) at the same point — so above/below your norm is visible at a glance.
 * Language is factual: a lower number is never praised on its own. */
export function CategoryBars({
  items,
  total,
  previousMonth,
  comparisonAvailable = true,
  baseline = "last",
  averageMonthCount,
  periodLabel,
  onDrill,
}: {
  items: CategoryPaceItem[];
  total: number;
  previousMonth: string;
  /** Whether the previous month was imported far enough to compare against. */
  comparisonAvailable?: boolean;
  baseline?: "last" | "average";
  averageMonthCount?: number;
  periodLabel?: string;
  onDrill?: (category: string) => void;
}) {
  if (items.length === 0) {
    return (
      <p className="guidance">
        Category totals appear once {periodLabel || "the selected period"} has spending.
      </p>
    );
  }
  const compareOf = (item: CategoryPaceItem): number | null =>
    baselineOf(item, baseline);
  const refLabel = baseline === "average" ? "your recent average" : "the same point last month";
  const markerLabel = baseline === "average"
    ? rollingAverageLabel(averageMonthCount, true)
    : previousMonth ? shortMonth(previousMonth) : "last month";
  // Whether there is a baseline at all is decided in categoryComparison.ts,
  // where it can be tested. Some callers (a plain proportional breakdown)
  // pass none, and an uncomparable previous month sends null deltas.
  const comparable = canCompare(items, baseline, comparisonAvailable, previousMonth);
  const max = Math.max(...items.map((i) => Math.max(i.amount, compareOf(i) ?? 0)), 1);
  return (
    <div className="cat-compare">
      {items.map((item) => {
        const compare = compareOf(item);
        const diff = differenceOf(item, baseline, comparable);
        const factual = categoryCaption(item, diff, refLabel, moneyCents);
        return (
          <button
            type="button"
            key={item.category}
            className="cat-row"
            onClick={() => onDrill?.(item.category)}
            title={`Open ${item.category} transactions`}
          >
            <div className="cat-row-head">
              <span className="cat-name">{item.category}</span>
              <strong>{moneyCents(item.amount)}</strong>
            </div>
            <div className="cat-bullet">
              <i className="cat-bar" style={{ width: `${Math.max(2, (item.amount / max) * 100)}%` }} />
              {comparable && compare != null && compare > 0 && (
                <span
                  className="cat-marker"
                  style={{ left: `${Math.min(100, (compare / max) * 100)}%` }}
                  title={`${markerLabel} at this day: ${moneyCents(compare)}`}
                />
              )}
            </div>
            <div className="cat-row-foot">
              <small className="cat-share">{item.share.toFixed(0)}% of spending</small>
              <small className={diff == null ? "" : diff > 0 ? "pace-up" : diff < 0 ? "pace-down" : "pace-flat"}>{factual}</small>
            </div>
          </button>
        );
      })}
      <p className="file-meta">
        Total {moneyCents(total)}{periodLabel ? ` · ${periodLabel}` : " · selected period"}.{comparable ? ` Marker shows ${markerLabel} at this day.` : ""}
      </p>
    </div>
  );
}

// Eight hues that stay separable on the SignalSpace navy and under the
// common CVD simulations. Ordered so the first four, which is all most
// breakdowns ever use, are maximally far apart.
const CATEGORY_COLORS = [
  "#1E9FFF",
  "#8B5CF6",
  "#22C55E",
  "#E3B341",
  "#F0883E",
  "#E05C7E",
  "#3FB6A8",
  "#8B99AA",
];

/** Spending-only category donut. The engine has already excluded transfers,
 * payments, income (including refunds), and investment movements. */
export function CategoryDonut({
  items,
  total,
  onDrill,
}: {
  items: CategoryPaceItem[];
  total: number;
  onDrill?: (category: string) => void;
}) {
  if (items.length === 0 || total <= 0) return null;

  let offset = 0;
  return (
    <div className="donut-wrap">
      <div className="donut-chart">
        <svg viewBox="0 0 120 120" role="img" aria-label={`Spending categories totaling ${moneyCents(total)}`}>
          <circle cx="60" cy="60" r="44" fill="none" stroke={VIZ.grid} strokeWidth="18" />
          {items.map((item, index) => {
            const percentage = item.share;
            const segmentOffset = offset;
            offset += percentage;
            return (
              <circle
                key={item.category}
                cx="60"
                cy="60"
                r="44"
                fill="none"
                stroke={CATEGORY_COLORS[index % CATEGORY_COLORS.length]}
                strokeWidth="18"
                pathLength="100"
                strokeDasharray={`${Math.max(0, percentage)} ${Math.max(0, 100 - percentage)}`}
                strokeDashoffset={-segmentOffset}
                transform="rotate(-90 60 60)"
                className="donut-slice"
                tabIndex={0}
                onClick={() => onDrill?.(item.category)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") onDrill?.(item.category);
                }}
              >
                <title>{`${item.category}: ${moneyCents(item.amount)} (${percentage.toFixed(1)}%)`}</title>
              </circle>
            );
          })}
        </svg>
        <div className="donut-center"><strong>{money(total)}</strong><span>spending</span></div>
      </div>
      <div className="donut-legend">
        {items.map((item, index) => (
          <button type="button" className="donut-legend-row" key={item.category} onClick={() => onDrill?.(item.category)}>
            <i style={{ background: CATEGORY_COLORS[index % CATEGORY_COLORS.length] }} />
            <span>{item.category}</span>
            <strong>{money(item.amount)} · {item.share.toFixed(1)}%</strong>
          </button>
        ))}
      </div>
    </div>
  );
}

/** Money-In sources only. The Python engine supplies both totals and
 * percentages; this component only renders them and emits a drill-down. */
export function IncomeSourceDonut({
  items,
  total,
  onDrill,
}: {
  items: IncomeSourceItem[];
  total: number;
  onDrill?: (source: string) => void;
}) {
  if (items.length === 0 || total <= 0) return null;
  let offset = 0;
  return (
    <div className="donut-wrap">
      <div className="donut-chart">
        <svg viewBox="0 0 120 120" role="img" aria-label={`Income sources totaling ${moneyCents(total)}`}>
          <circle cx="60" cy="60" r="44" fill="none" stroke={VIZ.grid} strokeWidth="18" />
          {items.map((item, index) => {
            const segmentOffset = offset;
            offset += item.pct;
            return (
              <circle
                key={`${item.source}-${item.category}`}
                cx="60" cy="60" r="44" fill="none"
                stroke={CATEGORY_COLORS[index % CATEGORY_COLORS.length]}
                strokeWidth="18" pathLength="100"
                strokeDasharray={`${Math.max(0, item.pct)} ${Math.max(0, 100 - item.pct)}`}
                strokeDashoffset={-segmentOffset}
                transform="rotate(-90 60 60)"
                className="donut-slice" tabIndex={0}
                onClick={() => onDrill?.(item.source)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") onDrill?.(item.source);
                }}
              >
                <title>{`${item.source}: ${moneyCents(item.total)} (${item.pct.toFixed(1)}%)`}</title>
              </circle>
            );
          })}
        </svg>
        <div className="donut-center"><strong>{money(total)}</strong><span>income</span></div>
      </div>
      <div className="donut-legend">
        {items.map((item, index) => (
          <button type="button" className="donut-legend-row" key={`${item.source}-${item.category}`} onClick={() => onDrill?.(item.source)} title={`Open income from ${item.source}`}>
            <i style={{ background: CATEGORY_COLORS[index % CATEGORY_COLORS.length] }} />
            <span>{item.source}<small>{item.tx_count} deposit{item.tx_count === 1 ? "" : "s"}</small></span>
            <strong>{money(item.total)} · {item.pct.toFixed(1)}%</strong>
          </button>
        ))}
      </div>
    </div>
  );
}

/**
 * Daily spending as a calendar. A month total says you spent $2,400; this says
 * you spent it on nine days, three of them weekends, and that last week was
 * quiet. Same money, but it describes a habit instead of a sum.
 *
 * Magnitude is carried by lightness on a single hue rather than by a rainbow,
 * so the ordering survives colour-vision deficiency, and every cell states its
 * own figure in the title attribute.
 */
export function SpendingCalendar({
  calendar, onDrill,
}: {
  calendar: SpendingPatterns["calendar"];
  onDrill?: (date: string) => void;
}) {
  if (!calendar.available) {
    return <p className="guidance">{calendar.reason || "Not available."}</p>;
  }
  const days = calendar.days || [];
  const spent = days.filter((d) => d.amount > 0).map((d) => d.amount);
  // Scale against a high percentile, not the maximum: one annual insurance
  // payment would otherwise flatten every ordinary day to the same shade.
  const sorted = [...spent].sort((a, b) => a - b);
  const ceiling = sorted.length
    ? sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.9))]
    : 0;

  const byMonth = new Map<string, CalendarDay[]>();
  for (const day of days) {
    const list = byMonth.get(day.month) || [];
    list.push(day);
    byMonth.set(day.month, list);
  }

  // The engine reports weekday 0 = Monday. Whether a week reads Monday-first
  // or Sunday-first is a regional habit, not a fact about the data, so it is
  // the reader's choice.
  const sundayFirst = readWeekStart() === "sunday";
  const dowLabels = sundayFirst
    ? ["S", "M", "T", "W", "T", "F", "S"]
    : ["M", "T", "W", "T", "F", "S", "S"];
  const column = (weekday: number) =>
    sundayFirst ? (weekday + 1) % 7 : weekday % 7;

  const shade = (amount: number) => {
    if (amount <= 0) return "transparent";
    const ratio = ceiling > 0 ? Math.min(1, amount / ceiling) : 1;
    // 22% to 62% lightness keeps even the lightest cell readable on the dark
    // surface, and the darkest from reading as pure black.
    return `hsl(263 62% ${22 + ratio * 40}%)`;
  };

  return (
    <div className="calendar-wrap">
      <div className="calendar-months">
        {[...byMonth.entries()].map(([month, monthDays]) => {
          const first = monthDays[0];
          const lead = first ? column(first.weekday) : 0;
          return (
            <div className="calendar-month" key={month}>
              <h5>{shortMonth(month)}</h5>
              <div className="calendar-grid" role="grid">
                {dowLabels.map((label, i) => (
                  <span className="calendar-dow" key={`${label}-${i}`}>{label}</span>
                ))}
                {Array.from({ length: lead }).map((_, i) => (
                  <span className="calendar-pad" key={`pad-${i}`} />
                ))}
                {monthDays.map((day) => (
                  <button
                    type="button"
                    key={day.date}
                    className={`calendar-cell${day.amount > 0 ? "" : " quiet"}`}
                    style={{ background: shade(day.amount) }}
                    title={`${day.date}: ${day.amount > 0 ? moneyCents(day.amount) : "nothing spent"}${day.count ? ` across ${day.count} transaction${day.count === 1 ? "" : "s"}` : ""}`}
                    onClick={() => day.amount > 0 && onDrill?.(day.date)}
                  >
                    <span>{Number(day.date.slice(8, 10))}</span>
                  </button>
                ))}
              </div>
            </div>
          );
        })}
      </div>
      <div className="calendar-key">
        <span>Nothing spent</span>
        <i style={{ background: shade(ceiling * 0.15) }} />
        <i style={{ background: shade(ceiling * 0.4) }} />
        <i style={{ background: shade(ceiling * 0.7) }} />
        <i style={{ background: shade(ceiling) }} />
        <span>{moneyCents(ceiling)}+</span>
      </div>
      <p className="chart-note">
        {calendar.spending_days} days with spending and {calendar.quiet_days}{" "}
        without, between {calendar.start} and {calendar.end}. A typical
        spending day is {moneyCents(calendar.typical_spending_day || 0)}.
        {calendar.busiest_day && calendar.busiest_day.amount > 0 && (
          <> The busiest was {calendar.busiest_day.date} at{" "}
          {moneyCents(calendar.busiest_day.amount)}.</>
        )}
      </p>
    </div>
  );
}

/** Average spend per weekday. Everyone believes they overspend at weekends. */
export function DayOfWeekBars({
  profile,
}: {
  profile: SpendingPatterns["day_of_week"];
}) {
  if (!profile.available) {
    return <p className="guidance">{profile.reason || "Not available."}</p>;
  }
  const days: DayOfWeekRow[] = profile.days || [];
  const max = niceMax(Math.max(...days.map((d) => d.average), 1));
  return (
    <div className="dow-wrap">
      <div className="dow-bars">
        {days.map((day) => {
          const height = max > 0 ? (day.average / max) * 100 : 0;
          const weekend = day.weekday >= 5;
          return (
            <div className="dow-col" key={day.weekday}>
              <strong className="dow-value">{money(day.average)}</strong>
              <div className="dow-track">
                <div
                  className="dow-fill"
                  style={{
                    height: `${Math.max(2, height)}%`,
                    background: weekend ? VIZ.spending : VIZ.accent,
                  }}
                  title={`${day.name}: ${moneyCents(day.average)} on an average ${day.name}, from ${day.transactions} transactions across ${day.occurrences} of them`}
                />
              </div>
              <span className={`dow-label${weekend ? " weekend" : ""}`}>
                {day.name.slice(0, 3)}
              </span>
            </div>
          );
        })}
      </div>
      <p className="chart-note">
        {profile.meaningful
          ? `Weekends average ${moneyCents(profile.weekend_average || 0)} a day against ${moneyCents(profile.weekday_average || 0)} on weekdays, a difference of ${moneyCents(Math.abs(profile.weekend_premium || 0))}.`
          : "No weekday stands out from the rest; your spending is spread fairly evenly across the week."}
        {" "}Measured over the last {profile.lookback_days} days, excluding
        fixed commitments.
      </p>
    </div>
  );
}

/**
 * What you kept, drawn as a share of what came in.
 *
 * The previous version drew the kept amount alone, scaled against the biggest
 * kept amount in the range. Two months could look identical while one kept
 * $600 out of $3,000 and the other $600 out of $9,000, which is the whole
 * question. Here the track is the month's income and the fill is what
 * survived it, so the rate is the shape of the bar rather than a number
 * printed beside it. A month that spent more than it earned is drawn from the
 * other side, in the spending colour, because it is not a small win.
 */
export function KeptChart({ months, onDrill }: {
  months: CashflowMonth[];
  onDrill?: (month: string, role: "income" | "spending" | "net") => void;
}) {
  if (!months.length) {
    return (
      <p className="guidance">
        Complete months appear here once a full month is imported.
      </p>
    );
  }
  const scale = Math.max(...months.map((m) => Math.max(m.income, Math.abs(m.net))), 1);
  const totalKept = months.reduce((sum, m) => sum + m.net, 0);
  const typicalRate = median(months.map((m) => m.savings_rate));
  const positive = months.filter((m) => m.net > 0).length;
  const monthEnd = (month: string) => {
    const [y, m] = month.split("-").map(Number);
    return `${month}-${String(new Date(y, m, 0).getDate()).padStart(2, "0")}`;
  };

  return (
    <div className="kept-chart">
      <div className="cf-facts">
        <div>
          <span>Kept in total</span>
          <strong className={totalKept >= 0 ? "good-text" : ""}>
            {totalKept >= 0 ? "+" : "−"}{money(Math.abs(totalKept))}
          </strong>
          <small>across {months.length} finished month{months.length === 1 ? "" : "s"}</small>
        </div>
        <div>
          <span>Typical rate</span>
          <strong>{typicalRate.toFixed(0)}%</strong>
          <small>of everything that came in</small>
        </div>
        <div>
          <span>Months ahead</span>
          <strong>{positive} of {months.length}</strong>
          <small>{positive === months.length
            ? "every month kept something"
            : `${months.length - positive} spent more than came in`}</small>
        </div>
      </div>
      {months.map((m, i) => {
        const prior = i > 0 ? months[i - 1] : null;
        const change = prior ? m.net - prior.net : null;
        const overspent = m.net < 0;
        return (
          <div className="kept-row" key={m.month}>
            <div className="kept-head">
              <span className="kept-month">{m.month}</span>
              <span className="kept-figures">
                <strong className={overspent ? "pace-up" : ""}>{money(m.net)}</strong>
                <small>{m.savings_rate.toFixed(0)}% of {money(m.income)}</small>
              </span>
            </div>
            <div className="kept-track"
              title={`${m.month}: ${moneyCents(m.income)} in, ${moneyCents(m.spending)} spent, ${moneyCents(m.net)} ${overspent ? "overspent" : "kept"}`}>
              <i className="kept-income" style={{ width: `${(m.income / scale) * 100}%` }} />
              <i className={overspent ? "kept-over" : "kept-net"}
                style={{ width: `${(Math.abs(m.net) / scale) * 100}%` }} />
            </div>
            <div className="kept-foot">
              <small>
                {overspent
                  ? `Spending was ${money(Math.abs(m.net))} more than came in`
                  : `${money(m.net)} survived the month`}
                {change != null && Math.abs(change) >= 25 && (
                  <em className={change > 0 ? "pace-down" : "pace-up"}>
                    {" "}{change > 0 ? "▲" : "▼"} {money(Math.abs(change))} vs {prior?.month}
                  </em>
                )}
              </small>
              <details className="kept-math">
                <summary>Show the math</summary>
                <div className="formula-list">
                  <button className="formula-drill" onClick={() => onDrill?.(m.month, "income")}>Money in <strong>{money(m.income)}</strong></button>
                  <button className="formula-drill" onClick={() => onDrill?.(m.month, "spending")}>Spending <strong>−{money(m.spending)}</strong></button>
                  <button className="formula-drill formula-total" onClick={() => onDrill?.(m.month, "net")}>Kept <strong>{money(m.net)}</strong></button>
                </div>
                <p className="chart-note">Covers {m.month}-01 to {monthEnd(m.month)}. Transfers and card payments are excluded from both sides.</p>
              </details>
            </div>
          </div>
        );
      })}
      <p className="file-meta">
        The pale bar is everything that came in; the solid bar is what was left
        at the end of the month.
      </p>
    </div>
  );
}

/**
 * How dependable the income itself is.
 *
 * The donut above this answers where money came from. It cannot answer the
 * question that actually changes a plan: whether that money shows up reliably,
 * and how much of it rests on one source continuing. Both are read from
 * finished months only, so a half-imported month cannot make income look like
 * it collapsed.
 */
export function IncomeSteadiness({ months, sources, total }: {
  months: CashflowMonth[];
  sources: IncomeSourceItem[];
  total: number;
}) {
  const complete = months.filter((m) => m.complete).slice(-12);
  const top = sources.length ? sources.reduce((a, b) => (b.total > a.total ? b : a)) : null;
  const concentration = top && total > 0 ? (top.total / total) * 100 : 0;

  if (complete.length < 2) {
    return (
      <div className="income-steady">
        <p className="guidance">
          How steady your income is needs two finished months. There
          {complete.length === 1 ? " is one" : " are none"} so far.
        </p>
        {top && concentration >= 50 && (
          <p className="chart-note">
            {concentration.toFixed(0)}% of income in this period came from{" "}
            {top.source}.
          </p>
        )}
      </div>
    );
  }

  const values = complete.map((m) => m.income);
  const typical = median(values);
  const low = Math.min(...values);
  const high = Math.max(...values);
  const swing = high > 0 ? (high - low) / high : 0;
  const steady = swing < 0.15;
  const scale = Math.max(high, 1);

  return (
    <div className="income-steady">
      <h4>How steady it is</h4>
      <div className="cf-facts cf-facts-tight">
        <div>
          <span>Typical month</span>
          <strong>{money(typical)}</strong>
          <small>median of {complete.length} finished month{complete.length === 1 ? "" : "s"}</small>
        </div>
        <div>
          <span>Range</span>
          <strong>{money(low)} – {money(high)}</strong>
          <small>{steady ? "steady month to month" : `a ${(swing * 100).toFixed(0)}% swing`}</small>
        </div>
        {top && (
          <div>
            <span>Largest source</span>
            <strong>{concentration.toFixed(0)}%</strong>
            <small>{top.source}</small>
          </div>
        )}
      </div>
      <div className="income-bars">
        {complete.map((m) => (
          <div className="income-bar" key={m.month}
            title={`${m.month}: ${moneyCents(m.income)} in`}>
            <div className="income-bar-track">
              <i style={{
                height: `${Math.max(3, (m.income / scale) * 100)}%`,
                background: m.income >= typical ? VIZ.income : VIZ.previous,
              }} />
            </div>
            <small>{m.month.slice(5)}</small>
          </div>
        ))}
      </div>
      <p className="chart-note">
        {steady
          ? `Income lands within ${money(high - low)} of itself each month, so planning from ${money(typical)} is reasonable.`
          : `Income ranges ${money(high - low)} between your best and weakest month, so a plan built on the good months will fall short in the quiet ones.`}
        {top && concentration >= 70 && ` ${concentration.toFixed(0)}% of it rests on ${top.source} alone.`}
      </p>
    </div>
  );
}

/** This month against the same month a year ago. The honest seasonal read. */
export function YearOverYearBars({
  yoy,
}: {
  yoy: SpendingPatterns["year_ago"];
}) {
  if (!yoy.available) {
    return <p className="guidance">{yoy.reason || "Not available."}</p>;
  }
  const rows = yoy.categories || [];
  const max = niceMax(Math.max(
    ...rows.map((r) => Math.max(r.current, r.year_ago)), 1,
  ));
  const up = (yoy.delta || 0) > 0;
  return (
    <div className="yoy-wrap">
      <p className="yoy-headline">
        <strong>{moneyCents(yoy.current_total || 0)}</strong> through day {yoy.through_day} of {yoy.month}
        against <strong>{moneyCents(yoy.year_ago_total || 0)}</strong> by the
        same day of {yoy.year_ago_month}.{" "}
        <em className={up ? "delta-up" : "delta-down"}>
          {up ? "▲" : "▼"} {moneyCents(Math.abs(yoy.delta || 0))}
          {" "}({Math.abs(yoy.percent || 0).toFixed(1)}%)
        </em>
      </p>
      <div className="yoy-rows">
        {rows.map((row) => (
          <div className="yoy-row" key={row.category}>
            <span className="yoy-name">{row.category}</span>
            <div className="yoy-track">
              <div
                className="yoy-then"
                style={{ width: `${(row.year_ago / max) * 100}%` }}
                title={`${yoy.year_ago_month}: ${moneyCents(row.year_ago)}`}
              />
              <div
                className="yoy-now"
                style={{ width: `${(row.current / max) * 100}%` }}
                title={`${yoy.month}: ${moneyCents(row.current)}`}
              />
            </div>
            <span className={`yoy-delta${row.delta > 0 ? " up" : " down"}`}>
              {row.delta > 0 ? "▲" : "▼"} {money(Math.abs(row.delta))}
            </span>
          </div>
        ))}
      </div>
      <p className="chart-note">
        Paler bars are {yoy.year_ago_month}, solid bars are {yoy.month}, both measured
        through day {yoy.through_day} so a part-month is never compared with a
        whole one.
      </p>
    </div>
  );
}

/**
 * Net worth followed month by month, rather than a figure typed into a box.
 *
 * The line is a starting point plus everything kept since. Where the user has
 * entered real balances those are drawn as points on top, because the gap
 * between the two is the interesting part: it is everything the statements do
 * not capture.
 */
/**
 * Net worth over time, drawn only from months someone actually recorded.
 *
 * The previous version of this chart drew two series: a "followed" line
 * projected from one typed starting figure plus each month's kept amount,
 * and a "measured" series from a table the desktop app never wrote. So the
 * confident line was extrapolation and the honest one was always empty.
 *
 * Every point here is a reading. There is nothing to project, which is why
 * there is only one line.
 *
 * The zero line is drawn whenever the range crosses it, because a net worth
 * below zero is a different situation from a small one and the shape of the
 * chart should say so without reading the axis.
 */
export function NetWorthTrendChart({ overview }: { overview: NetWorthOverview }) {
  const points = overview.entries;
  if (points.length < 2) {
    return (
      <p className="guidance">
        {points.length === 1
          ? `One reading so far, ${moneyCents(points[0].net)} in ${shortMonth(points[0].month)}. Record next month and the trend starts.`
          : overview.reason || "No readings yet."}
      </p>
    );
  }
  const W = Math.max(360, 80 + points.length * 46);
  const H = 240;
  const padL = 62, padR = 14, padT = 16, padB = 30;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;

  const values = points.map((p) => p.net);
  const rawMin = Math.min(...values);
  const rawMax = Math.max(...values);
  const span = rawMax - rawMin || Math.max(1, Math.abs(rawMax) * 0.1);
  const lo = rawMin - span * 0.12;
  const hi = rawMax + span * 0.12;
  const x = (i: number) => padL + (i / Math.max(1, points.length - 1)) * plotW;
  const y = (v: number) => padT + plotH - ((v - lo) / (hi - lo)) * plotH;

  const line = points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.net)}`).join(" ");
  const baseline = lo <= 0 && hi >= 0 ? 0 : lo;
  const area = `${line} L${x(points.length - 1)},${y(baseline)} L${x(0)},${y(baseline)} Z`;
  const total = overview.since_first?.amount ?? 0;
  const rising = total >= 0;
  const stroke = rising ? VIZ.income : "#F0883E";
  const crossesZero = lo < 0 && hi > 0;

  return (
    <div className="nw-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} role="img"
        aria-label={`Net worth across ${points.length} recorded months, ${rising ? "up" : "down"} ${moneyCents(Math.abs(total))} overall`}
        style={{ width: "100%", height: "auto", display: "block" }}>
        {[0, 0.5, 1].map((f) => {
          const v = lo + (hi - lo) * f;
          return (
            <g key={f}>
              <line x1={padL} x2={W - padR} y1={y(v)} y2={y(v)}
                stroke={VIZ.grid} strokeWidth="1" />
              <text x={padL - 8} y={y(v) + 4} textAnchor="end" fontSize="10"
                fill={VIZ.ink}>{money(v)}</text>
            </g>
          );
        })}
        {crossesZero && (
          <g>
            <line x1={padL} x2={W - padR} y1={y(0)} y2={y(0)}
              stroke={VIZ.ink} strokeWidth="1.5" strokeDasharray="4 3" />
            <text x={W - padR} y={y(0) - 5} textAnchor="end" fontSize="10"
              fill={VIZ.ink}>zero</text>
          </g>
        )}
        <path d={area} fill={stroke} opacity="0.12" />
        <path d={line} fill="none" stroke={stroke} strokeWidth="2.5"
          strokeLinejoin="round" />
        {points.map((p, i) => (
          <g key={p.month}>
            <circle cx={x(i)} cy={y(p.net)} r="3.5" fill={stroke}>
              <title>
                {`${p.month} — ${moneyCents(p.net)}${p.change == null ? " (first reading)" : `, ${p.change >= 0 ? "up" : "down"} ${moneyCents(Math.abs(p.change))} on the month`}
cash ${moneyCents(p.cash)} · investments ${moneyCents(p.investments)} · other ${moneyCents(p.other_assets)} · debts ${moneyCents(p.liabilities)}`}
              </title>
            </circle>
            {(i === 0 || i === points.length - 1 || points.length <= 8) && (
              <text x={x(i)} y={H - 9} textAnchor="middle" fontSize="10"
                fill={VIZ.ink}>{shortMonth(p.month)}</text>
            )}
          </g>
        ))}
      </svg>
      <p className="file-meta">
        {points.length} recorded month{points.length === 1 ? "" : "s"}, {shortMonth(points[0].month)} to {shortMonth(points[points.length - 1].month)}.
        {" "}Every point is a reading you entered; nothing here is estimated.
      </p>
    </div>
  );
}
