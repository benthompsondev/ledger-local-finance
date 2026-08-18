import { useState } from "react";
import { DayOfWeekBars } from "./charts";
import type { Insight, InsightExplanation, SpendingPatterns } from "./types";

/**
 * One thing SignalSpace noticed.
 *
 * The claim is rendered exactly as the engine wrote it, figures included, and
 * it stands on its own with no AI configured. "Explain this" is additive: it
 * sends only this one finding, so the model has nothing to compute and a few
 * hundred bytes of context instead of a financial history.
 */
export function InsightCard({
  insight, canExplain, onExplain, onDrill,
}: {
  insight: Insight;
  canExplain: boolean;
  onExplain: (findingId: string) => Promise<{ explanation: InsightExplanation }>;
  onDrill?: (drill: NonNullable<Insight["drill"]>) => void;
}) {
  const [explanation, setExplanation] = useState<InsightExplanation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const explain = async () => {
    setBusy(true);
    setError("");
    try {
      setExplanation((await onExplain(insight.id)).explanation);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <article className={`insight-card${insight.tone === "good" ? " good" : ""}`}>
      <h4>{insight.title}</h4>
      <p className="insight-claim">{insight.claim}</p>

      {insight.chart === "day_of_week" && insight.days && (
        <div style={{ marginTop: 12 }}>
          <DayOfWeekBars profile={{
            available: true, reason: "", days: insight.days,
            meaningful: true,
            weekend_average: Number(insight.weekend_average ?? 0),
            weekday_average: Number(insight.weekday_average ?? 0),
            weekend_premium: Number(insight.premium ?? 0),
            lookback_days: 180,
          } as SpendingPatterns["day_of_week"]} />
        </div>
      )}

      <div className="insight-meta">
        <span className="chip">{insight.confidence} confidence</span>
        <span className="chip">calculated locally</span>
        {insight.drill && onDrill && (
          <button type="button" className="link-button"
            onClick={() => onDrill(insight.drill!)}>
            Show the transactions
          </button>
        )}
        {canExplain && !explanation && (
          <button type="button" className="link-button" disabled={busy}
            onClick={() => void explain()}>
            {busy ? "Asking…" : "Explain this"}
          </button>
        )}
      </div>

      {error && <div className="inline-error">{error}</div>}

      {explanation && (
        <div className="insight-explain" aria-live="polite">
          <span className="from-ai">
            {explanation.provider} · {explanation.model} · {explanation.bytes_sent} bytes sent
            {explanation.unsupported_removed.length > 0
              && ` · ${explanation.unsupported_removed.length} unverified figure removed`}
          </span>
          {explanation.text}
        </div>
      )}
    </article>
  );
}
