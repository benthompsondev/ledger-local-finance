import { useState } from "react";
import { money, moneyCents } from "./money";
import type { TxPrefill, UpcomingItem, UpcomingMoney } from "./types";

/**
 * The next few weeks of money SignalSpace already recognises.
 *
 * Everything here is an *expectation drawn from a rhythm*, never a
 * prediction. The wording is deliberate throughout: "expected around", never
 * "will". No balance is shown, computed, or implied — this component receives
 * events and their sums and does no arithmetic on money at all.
 *
 * Expected items are drawn in a lighter register than anything on Home that
 * describes something which actually happened, so the two can never be
 * mistaken for each other at a glance.
 */

const DAY_MS = 86_400_000;

function dayLabel(iso: string): string {
  const when = new Date(`${iso}T00:00:00`);
  return when.toLocaleDateString("en-CA", { month: "short", day: "numeric" });
}

/**
 * Three buckets, not five. Grouping strictly by calendar week gave a heading
 * for every one or two rows, which is more furniture than structure over a
 * four-week window.
 */
function bucketOf(iso: string, todayIso: string): string {
  const days = Math.round(
    (new Date(`${iso}T00:00:00`).getTime()
      - new Date(`${todayIso}T00:00:00`).getTime()) / DAY_MS,
  );
  if (days <= 6) return "This week";
  if (days <= 13) return "Next week";
  return "Later this window";
}

const BUCKET_ORDER = ["This week", "Next week", "Later this window"];

function EventRow({
  item, onDrill,
}: {
  item: UpcomingItem;
  onDrill?: (prefill: TxPrefill) => void;
}) {
  const [open, setOpen] = useState(false);
  const income = item.kind === "income";

  return (
    <div className={`radar-row${income ? " income" : ""}`}>
      <button type="button" className="radar-row-head"
        aria-expanded={open} onClick={() => setOpen(!open)}>
        {/* One line each. "Expected around" is said once at the top of the
            card rather than seven times down it; the only per-row date cue
            is the one that differs from the rest. */}
        <span className="radar-when">{dayLabel(item.expected_date)}</span>
        <span className="radar-what">
          {item.label}
          <small>
            {item.cadence_note} · {item.confidence}
          </small>
        </span>
        <span className={`radar-amount${income ? " income" : ""}`}>
          {income ? "+" : "−"}{moneyCents(item.amount)}
        </span>
      </button>

      {open && (
        <div className="radar-evidence">
          {item.recent.length > 0 ? (
            <dl>
              {item.recent.map((charge) => (
                <div key={charge.date}>
                  <dt>{dayLabel(charge.date)}</dt>
                  <dd>{moneyCents(charge.amount)}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className="radar-note">
              Worked out from how regularly this has arrived, rather than from
              a set amount.
            </p>
          )}
          <p className="radar-note">
            {item.last_seen && `Last seen ${dayLabel(item.last_seen)}. `}
            This is what the rhythm suggests, not a scheduled payment.
          </p>
          {item.drill && onDrill && (
            <button type="button" className="ghost-button"
              onClick={() => onDrill({
                search: item.drill?.merchant || item.drill?.search,
                category: item.drill?.category,
              })}>
              Show the transactions
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export function MoneyRadar({
  data, onDrill,
}: {
  data: UpcomingMoney;
  onDrill?: (prefill: TxPrefill) => void;
}) {
  if (!data.available) {
    return (
      <article className="chart-card radar-card">
        <div className="chart-card-head">
          <div>
            <h3>The next few weeks</h3>
            <p className="chart-explainer">{data.reason}</p>
          </div>
        </div>
      </article>
    );
  }

  const buckets = new Map<string, UpcomingItem[]>();
  for (const item of data.items) {
    const key = bucketOf(item.expected_date, data.window_start);
    buckets.set(key, [...(buckets.get(key) ?? []), item]);
  }
  const ordered = BUCKET_ORDER
    .filter((name) => buckets.has(name))
    .map((name) => [name, buckets.get(name)!] as const);

  return (
    <article className="chart-card radar-card">
      <div className="chart-card-head">
        <div>
          <h3>The next few weeks</h3>
          <p className="chart-explainer">
            Bills and paydays SignalSpace has strong reason to expect, placed on
            the dates their rhythm suggests. These are expectations, not
            scheduled payments.
          </p>
        </div>
        <span className={`radar-through radar-${data.staleness}`}>
          Based on statements through {dayLabel(data.data_through)}
        </span>
      </div>

      {data.staleness_note && (
        <p className="radar-stale">{data.staleness_note}</p>
      )}

      <div className="radar-totals">
        <div>
          <span>Expected in</span>
          <strong className="income">{money(data.expected_in_total)}</strong>
          <small>{data.income_count} payday{data.income_count === 1 ? "" : "s"}</small>
        </div>
        <div>
          <span>Expected out</span>
          <strong>{money(data.expected_out_total)}</strong>
          <small>{data.bill_count} bill{data.bill_count === 1 ? "" : "s"}</small>
        </div>
      </div>

      {data.summary && <p className="radar-summary">{data.summary}</p>}

      <div className="radar-weeks">
        {ordered.map(([name, items]) => (
          <section key={name}>
            <h4>{name}</h4>
            {items.map((item, index) => (
              <EventRow key={`${item.key}-${item.expected_date}-${index}`}
                item={item} onDrill={onDrill} />
            ))}
          </section>
        ))}
      </div>
    </article>
  );
}
