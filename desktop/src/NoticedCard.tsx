import { moneyCents, signedMoneyCents } from "./money";
import type { Insight, InsightEvidence } from "./types";

/**
 * One thing SpendShape noticed, on the Insights page.
 *
 * Deliberately not `InsightCard`. That one belongs to Coach, where the job is
 * a short claim plus an optional model-written explanation. Here the job is
 * the opposite: no model at all, but the working shown — the figure, the
 * comparison it came from, the rows behind it, and a way into the
 * transactions.
 *
 * Every number arrives computed. This file formats and lays out; it never
 * adds, subtracts or compares amounts, because the moment two places can
 * calculate the same figure they eventually disagree.
 */

function evidenceValue(row: InsightEvidence): string {
  if (row.value == null) return "";
  if (row.kind === "count") return String(row.value);
  if (row.kind === "text") return row.note;
  return row.signed ? signedMoneyCents(row.value) : moneyCents(row.value);
}

export function NoticedCard({
  insight, onDrill,
}: {
  insight: Insight;
  onDrill?: (drill: NonNullable<Insight["drill"]>) => void;
}) {
  const good = insight.tone === "good";
  const rows = insight.evidence ?? [];
  const figure = insight.figure;
  const hasFigure = figure != null && insight.figure_kind !== "";

  return (
    <article className={`noticed-card${good ? " good" : ""}`}>
      <h4>{insight.title}</h4>

      {hasFigure && (
        <p className="noticed-figure">
          {/* Cents, not whole dollars. The claim underneath quotes the same
              amount to the penny, and a headline reading $278 above a
              sentence reading $277.67 looks like two different figures. */}
          <strong>
            {insight.figure_kind === "count"
              ? String(figure)
              : moneyCents(Math.abs(figure))}
          </strong>
          <span>{insight.figure_caption}</span>
        </p>
      )}

      <p className="noticed-claim">{insight.claim}</p>

      {/* Closed by default. Open, four of these filled the screen before the
          charts started, which is the dashboard this was meant not to be.
          The claim already carries the figures; this is the working. */}
      {rows.length > 0 && (
        <details className="noticed-working">
          <summary>Show the working</summary>
          <dl className="noticed-evidence">
            {rows.map((row) => (
              <div key={`${row.label}-${row.note}`}>
                <dt>
                  {row.label}
                  {row.note && row.kind !== "text" && <small>{row.note}</small>}
                </dt>
                <dd>{evidenceValue(row)}</dd>
              </div>
            ))}
          </dl>
        </details>
      )}

      <div className="noticed-foot">
        {insight.evidence_note && (
          <p className="noticed-basis">{insight.evidence_note}</p>
        )}
        {insight.drill && onDrill && (
          <button type="button" className="ghost-button"
            onClick={() => onDrill(insight.drill!)}>
            Show the transactions
          </button>
        )}
      </div>
    </article>
  );
}
