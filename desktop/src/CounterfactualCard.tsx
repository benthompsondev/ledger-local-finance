/**
 * What if — replay a finished month with one spending change applied.
 *
 * Nothing here is a forecast. The user picks a month they already lived, one
 * category or merchant, and how much less they might have spent; the engine
 * re-runs that month's real transactions and returns both totals. This file
 * arranges and formats them and performs no arithmetic on money, so the
 * replay can never become a second answer to a question the engine already
 * answered.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { loadCounterfactual } from "./api";
import { ReplayChart } from "./charts";
import { money, moneyCents } from "./money";
import { createRequestGate } from "./planPreview";
import { visibleReplay } from "./counterfactualState";
import type { CounterfactualPayload, ReplayTarget } from "./types";

interface Props {
  refreshToken: number;
}

const REDUCTIONS = [
  { pct: 25, label: "a quarter less" },
  { pct: 50, label: "half as much" },
  { pct: 100, label: "none at all" },
];

const ordinal = (n: number) => {
  const suffix = n % 100 >= 11 && n % 100 <= 13 ? "th"
    : ["th", "st", "nd", "rd"][n % 10] ?? "th";
  return `${n}${suffix}`;
};

const rankPhrase = (position: number, total: number) =>
  position === 1 ? "your best finished month"
    : position === total ? "your weakest finished month"
      : `your ${ordinal(position)} best of ${total}`;

export default function CounterfactualCard({ refreshToken }: Props) {
  const [data, setData] = useState<CounterfactualPayload | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [month, setMonth] = useState("");
  const [target, setTarget] = useState<ReplayTarget | null>(null);
  const [pct, setPct] = useState(100);
  const [showEvidence, setShowEvidence] = useState(false);

  // Clicking through chips issues one sidecar process per click, so responses
  // finishing out of order is ordinary here. Only the newest may apply, or a
  // slow reply from an abandoned chip can overwrite the chosen one.
  const gate = useRef(createRequestGate());

  const run = useCallback(async (spec: {
    month?: string; target?: ReplayTarget | null; pct?: number;
  }) => {
    const token = gate.current.begin();
    setBusy(true);
    setError("");
    try {
      const next = await loadCounterfactual({
        month: spec.month,
        targetKind: spec.target?.kind,
        targetKey: spec.target?.key,
        // Meaningless without something to reduce, so it is not sent.
        reductionPct: spec.target ? spec.pct : undefined,
      });
      if (!gate.current.isLatest(token)) return;
      setData(next);
      setMonth(next.month);
      if ("target" in spec) setTarget(spec.target ?? null);
      if (spec.pct !== undefined) setPct(spec.pct);
      setError("");
    } catch (cause) {
      if (!gate.current.isLatest(token)) return;
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      if (gate.current.isLatest(token)) setBusy(false);
    }
  }, []);

  // What is on screen right now, so a data refresh can re-ask the same
  // question. Re-running blank would leave the chosen chip lit with no
  // result underneath it.
  const selection = useRef({ month: "", target: null as ReplayTarget | null, pct: 100 });
  selection.current = { month, target, pct };

  useEffect(() => {
    const { month: m, target: t, pct: p } = selection.current;
    void run({ month: m || undefined, target: t, pct: p });
  }, [run, refreshToken]);

  const chooseMonth = (next: string) => {
    setShowEvidence(false);
    void run({ month: next, target: null, pct });
  };

  const chooseTarget = (next: ReplayTarget) => {
    const same = target?.kind === next.kind && target?.key === next.key;
    const chosen = same ? null : next;
    setShowEvidence(false);
    void run({ month, target: chosen, pct });
  };

  const choosePct = (next: number) => {
    void run({ month, target, pct: next });
  };

  // An error must not replace the card. Every screen action is its own
  // short-lived sidecar, so a single failed request is recoverable — losing
  // the month picker and the chips would strand the user with nothing to
  // press.
  if (!data) {
    return error ? (
      <article className="chart-card">
        <h3>What if</h3>
        <div className="inline-error" role="alert">{error}</div>
        <div className="button-row">
          <button type="button" onClick={() => void run({})}>Try again</button>
        </div>
      </article>
    ) : null;
  }

  if (!data.available) {
    return (
      <article className="chart-card">
        <h3>What if</h3>
        <p className="guidance">{data.reason}</p>
      </article>
    );
  }

  const categories = data.targets.filter((t) => t.kind === "category");
  const merchants = data.targets.filter((t) => t.kind === "merchant");
  const replay = busy ? null : visibleReplay(data, error, { month, target, pct });
  const isActive = (t: ReplayTarget) =>
    target?.kind === t.kind && target?.key === t.key;

  return (
    <article className="chart-card wide-card replay-card">
      <div className="chart-card-head">
        <div>
          <h3>What if you had spent less in {data.month_label}?</h3>
          <p className="chart-explainer">
            Pick something you actually spent on and Northstar re-runs that
            month from your real transactions. Nothing here is predicted —
            it is the month you already had, with one thing changed.
          </p>
        </div>
        <label className="period-control">
          Month
          <select value={month} disabled={busy}
            onChange={(event) => chooseMonth(event.target.value)}>
            {data.months.map((m) => (
              <option key={m.month} value={m.month}>{m.label}</option>
            ))}
          </select>
        </label>
      </div>

      {error && <>
        <div className="inline-error" role="alert">{error}</div>
        <div className="button-row">
          <button type="button" disabled={busy}
            onClick={() => void run({ month, target, pct })}>
            Try again
          </button>
        </div>
      </>}

      <div className="replay-picker">
        <span className="eyebrow">Spend less on</span>
        <div className="replay-chips">
          {categories.map((t) => (
            <button type="button" key={`c-${t.key}`} disabled={busy}
              className={isActive(t) ? "replay-chip replay-chip-on" : "replay-chip"}
              onClick={() => chooseTarget(t)}>
              {t.label}<small>{money(t.amount)}</small>
            </button>
          ))}
        </div>
        {merchants.length > 0 && <>
          <span className="eyebrow">Or one merchant</span>
          <div className="replay-chips">
            {merchants.slice(0, 8).map((t) => (
              <button type="button" key={`m-${t.key}`} disabled={busy}
                className={isActive(t) ? "replay-chip replay-chip-on" : "replay-chip"}
                onClick={() => chooseTarget(t)}>
                {t.label || "Unknown"}<small>{money(t.amount)}</small>
              </button>
            ))}
          </div>
        </>}
      </div>

      {!target && (
        <p className="guidance replay-prompt">
          Choose one above to see what {data.month_label} would have looked
          like without it.
        </p>
      )}

      {target && replay && !replay.available && (
        <p className="guidance">{replay.reason}</p>
      )}

      {target && replay?.available && replay.actual && replay.counterfactual && (
        <div className="replay-result">
          <div className="segmented-control replay-dial" aria-label="How much less">
            {REDUCTIONS.map((option) => (
              <button type="button" key={option.pct} disabled={busy}
                className={pct === option.pct ? "selected" : ""}
                onClick={() => choosePct(option.pct)}>
                {option.label}
              </button>
            ))}
          </div>

          <p className="replay-headline">
            Spending {replay.removed_entirely ? "nothing" : `${String(replay.reduction_pct)}% less`}
            {" "}on <strong>{replay.target?.label}</strong> would have freed{" "}
            <strong className="good-text">{moneyCents(replay.freed ?? 0)}</strong>
            {" "}across {replay.matched_count} purchase
            {replay.matched_count === 1 ? "" : "s"}.
          </p>

          <div className="nw-facts">
            <div>
              <span>You kept</span>
              <strong>{moneyCents(replay.actual.net)}</strong>
              <small>what {data.month_label} actually closed at</small>
            </div>
            <div>
              <span>You would have kept</span>
              <strong className="good-text">
                {moneyCents(replay.counterfactual.net)}
              </strong>
              <small>the same month, with that one change</small>
            </div>
            <div>
              <span>Savings rate</span>
              <strong>{replay.counterfactual.savings_rate.toFixed(1)}%</strong>
              <small>
                instead of {replay.actual.savings_rate.toFixed(1)}% of money in
              </small>
            </div>
          </div>

          {replay.rank && replay.months && (
            <>
              <ReplayChart months={replay.months} />
              <p className="replay-rank">
                {replay.rank.improved ? (
                  <>
                    {data.month_label} was{" "}
                    {rankPhrase(replay.rank.actual, replay.rank.total)}. That
                    one change would have made it{" "}
                    <strong>{rankPhrase(replay.rank.counterfactual, replay.rank.total)}</strong>.
                  </>
                ) : (
                  <>
                    {data.month_label} stays{" "}
                    {rankPhrase(replay.rank.counterfactual, replay.rank.total)}
                    {" "}either way — the change is real, but not enough to move
                    the month against the others.
                  </>
                )}
              </p>
            </>
          )}

          <button type="button" className="link-button"
            onClick={() => setShowEvidence((open) => !open)}>
            {showEvidence ? "Hide" : "Show"} the {replay.matched_count} transaction
            {replay.matched_count === 1 ? "" : "s"} behind this
          </button>
          {showEvidence && replay.transactions && (
            <div className="replay-evidence">
              {replay.transactions.map((row, index) => (
                <div className="source-row" key={`${row.date}-${index}`}>
                  <span>{row.description || "Unknown"}
                    <small>{row.date} · {row.category}</small></span>
                  <strong>
                    {moneyCents(row.amount)}
                    {!replay.removed_entirely && (
                      <small> → {moneyCents(row.replayed_amount)}</small>
                    )}
                  </strong>
                </div>
              ))}
              {replay.evidence_truncated && (
                <p className="file-meta">
                  Showing the largest purchases only. Every matching
                  transaction is included in the totals above.
                </p>
              )}
            </div>
          )}

          <p className="file-meta replay-caveat">
            This is what {data.month_label} would have added up to, not a
            prediction about a future month.
          </p>
        </div>
      )}
    </article>
  );
}
