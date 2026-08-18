import { useEffect, useState } from "react";
import { saveNetWorthEntry } from "./api";
import { money } from "./money";
import {
  fieldsFor, formIdentity, parseReading, runningTotal,
  type ReadingFields,
} from "./netWorthForm";
import type { NetWorthEntry, NetWorthOverview, NetWorthPrefill } from "./types";

/**
 * One reading a month: four figures, prefilled from whatever balances are
 * already entered.
 *
 * The version this replaces asked for a single starting figure and then
 * projected forward using what the statements said was kept each month. That
 * avoided monthly data entry, which was the right instinct, but it produced
 * a line that could not see a brokerage account move or a mortgage come
 * down, and it drew that line as confidently as a measurement.
 *
 * Four numbers once a month is a small enough habit to keep, and the prefill
 * makes it a review rather than data entry. Every prefilled figure stays
 * editable because SpendShape only knows the accounts it has balances for,
 * and most people own something it has never heard of.
 *
 * The form state is keyed on which reading is being shown rather than seeded
 * once at mount. InsightsView mounts this inside a closed details element,
 * so a useState initializer ran long before the user clicked Edit, and the
 * selected reading never appeared in the fields.
 */
export function NetWorthEntryForm({
  prefill, existing, onSaved, onCancel,
}: {
  prefill: NetWorthPrefill;
  /** When correcting a month that is already recorded. */
  existing?: NetWorthEntry;
  onSaved: (overview: NetWorthOverview) => void;
  onCancel?: () => void;
}) {
  const identity = formIdentity(existing, prefill.month);
  const [fields, setFields] = useState<ReadingFields>(
    () => fieldsFor(existing, prefill),
  );
  const [shownFor, setShownFor] = useState(identity);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Reload only when the *identity* changes: picking a different month to
  // edit, or leaving edit mode. A parent refresh that returns the same
  // reading leaves an edit in progress alone.
  useEffect(() => {
    if (shownFor === identity) return;
    setFields(fieldsFor(existing, prefill));
    setShownFor(identity);
    setError("");
  }, [identity, shownFor, existing, prefill]);

  const set = (field: keyof ReadingFields) =>
    (event: { target: { value: string } }) =>
      setFields((current) => ({ ...current, [field]: event.target.value }));

  const running = runningTotal(fields);

  const save = async () => {
    const parsed = parseReading(fields);
    if (!parsed.ok) {
      setError(parsed.reason);
      return;
    }
    setBusy(true);
    setError("");
    try {
      onSaved(await saveNetWorthEntry(parsed.reading));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  const usedFor = (field: string) =>
    prefill.sources.filter((source) => source.field === field);

  return (
    <div className="nw-entry-form">
      <div className="form-grid">
        <label>Month
          <input type="month" value={fields.month} max={prefill.month}
            onChange={set("month")} />
          <small>Saving a month twice corrects it. It never doubles up.</small>
        </label>
        <label>Cash
          <input type="number" inputMode="decimal" value={fields.cash}
            placeholder="0" onChange={set("cash")} />
          <small>
            Chequing, savings and cash.
            {usedFor("cash").length > 0
              && ` Prefilled from ${usedFor("cash").map((s) => s.account).join(", ")}.`}
          </small>
        </label>
        <label>Investments
          <input type="number" inputMode="decimal" value={fields.investments}
            placeholder="0" onChange={set("investments")} />
          <small>
            Brokerage, retirement, anything with a market value. An estimate is
            fine; you are tracking direction, not filing a return.
          </small>
        </label>
        <label>Other assets
          <input type="number" inputMode="decimal" value={fields.otherAssets}
            placeholder="0" onChange={set("otherAssets")} />
          <small>A car, a property, anything else you want counted.</small>
        </label>
        <label>Debts
          <input type="number" inputMode="decimal" value={fields.liabilities}
            placeholder="0" onChange={set("liabilities")} />
          <small>
            Cards, loans, a mortgage. Enter what you owe as a positive number.
          </small>
        </label>
      </div>
      <p className="nw-running">
        {running == null ? (
          <>
            <strong>Fill in all four figures</strong>
            <small>
              {" "}A blank field is not the same as zero, so nothing is
              assumed. Enter 0 where the answer really is zero.
            </small>
          </>
        ) : (
          <>
            This month: <strong>{money(running)}</strong>
            <small>
              {" "}cash {money(Number(fields.cash))} + investments{" "}
              {money(Number(fields.investments))} + other{" "}
              {money(Number(fields.otherAssets))} − debts{" "}
              {money(Number(fields.liabilities))}
            </small>
          </>
        )}
      </p>
      {prefill.excluded.length > 0 && (
        <p className="nw-excluded">
          <strong>
            {prefill.excluded.length} balance
            {prefill.excluded.length === 1 ? " is" : "s are"} not in{" "}
            {prefill.local_currency}
          </strong>
          <small>
            {prefill.excluded
              .map((s) => `${s.account} (${s.currency} ${s.amount.toFixed(2)})`)
              .join(", ")}
            . SpendShape does not convert currencies, so {prefill.excluded.length === 1
              ? "it is"
              : "they are"}{" "}
            left out of the figures above. Add a converted amount by hand if
            you want {prefill.excluded.length === 1 ? "it" : "them"} counted.
          </small>
        </p>
      )}
      <label className="nw-note">Note
        <input value={fields.note} placeholder="Counted the car, left the mortgage out"
          onChange={set("note")} />
      </label>
      <div className="button-row">
        <button type="button" disabled={busy || running == null}
          onClick={() => void save()}>
          {busy ? "Saving…" : existing ? `Update ${fields.month}` : `Record ${fields.month}`}
        </button>
        {onCancel && (
          <button type="button" className="ghost-button" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
      {error && <div className="inline-error">{error}</div>}
    </div>
  );
}
