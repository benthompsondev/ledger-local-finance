/**
 * Money Focus — one thing you are saving for, funded by measured months.
 *
 * The goal screen this replaces asked people to type their own progress in,
 * which meant the number was only ever as true as the last time someone
 * remembered. Northstar already knows what was kept in each finished month,
 * so the progress here is read, not entered. The current month is shown
 * beside the total rather than added to it, because a part-month of spending
 * against a whole month of pay always flatters the figure.
 */
import { useCallback, useEffect, useState } from "react";
import { clearMoneyFocus, loadMoneyFocus, saveMoneyFocus } from "./api";
import { money, moneyCents } from "./money";
import { VIZ } from "./charts";
import type { MoneyFocus } from "./types";

interface Props {
  refreshToken?: number;
  /** Home shows the one-line read; Plan owns the full card and the editing. */
  compact?: boolean;
  onOpen?: () => void;
}

const blank = { name: "", target: "", date: "", already: "0", start: "" };

export default function MoneyFocusCard({ refreshToken, compact, onOpen }: Props) {
  const [data, setData] = useState<MoneyFocus | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState(blank);
  const [editing, setEditing] = useState(false);

  const refresh = useCallback(async () => {
    try { setData(await loadMoneyFocus()); setError(""); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh, refreshToken]);

  const startEditing = () => {
    const f = data?.focus;
    setForm(f ? {
      name: f.name, target: String(f.target_amount),
      date: f.target_date, already: String(f.already_saved),
      start: f.started_month,
    } : { ...blank, start: data?.suggested_start ?? "" });
    setEditing(true);
  };

  const save = async () => {
    setBusy(true); setError("");
    try {
      setData(await saveMoneyFocus({
        name: form.name, targetAmount: Number(form.target),
        startedMonth: form.start, targetDate: form.date,
        alreadySaved: Number(form.already || 0),
      }));
      setEditing(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally { setBusy(false); }
  };

  const remove = async () => {
    setBusy(true);
    try { setData(await clearMoneyFocus()); setEditing(false); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  };

  if (!data) return null;

  // ── the one-line read for Home ────────────────────────────────────────
  if (compact) {
    if (!data.available || !data.focus) return null;
    const pct = data.progress_pct ?? 0;
    return (
      <button type="button" className="focus-strip" onClick={onOpen}>
        <div className="focus-strip-head">
          <span><strong>{data.focus.name}</strong> · {money(data.saved ?? 0)} of {money(data.target ?? 0)}</span>
          <em>{pct.toFixed(0)}%</em>
        </div>
        <div className="progress-track"><span style={{ width: `${pct}%` }} /></div>
        <small>{data.sentence}</small>
      </button>
    );
  }

  // ── setup ─────────────────────────────────────────────────────────────
  if (editing || (!data.available && !error)) {
    const canSave = form.name.trim() && Number(form.target) > 0 && form.start;
    return (
      <article className="chart-card focus-card">
        <div className="chart-card-head">
          <div>
            <h3>{data.available ? "Edit your focus" : "Choose one money focus"}</h3>
            <p className="chart-explainer">{data.reason || "Northstar funds this from what you keep each finished month, so you never update it by hand."}</p>
          </div>
        </div>
        {error && <div className="inline-error">{error}</div>}
        <div className="form-grid">
          <label>What are you saving for?
            <input value={form.name} placeholder="Emergency fund"
              onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </label>
          <label>Target amount
            <input type="number" min="1" step="1" value={form.target}
              onChange={(e) => setForm({ ...form, target: e.target.value })} />
          </label>
          <label>Already put aside
            <input type="number" min="0" step="1" value={form.already}
              onChange={(e) => setForm({ ...form, already: e.target.value })} />
            <small>What this focus starts at. Everything after is measured.</small>
          </label>
          <label>Count from
            <input type="month" value={form.start}
              onChange={(e) => setForm({ ...form, start: e.target.value })} />
            <small>Finished months from here on fund it.</small>
          </label>
          <label>Target date (optional)
            <input type="date" value={form.date}
              onChange={(e) => setForm({ ...form, date: e.target.value })} />
            <small>Northstar will say whether your real pace meets it.</small>
          </label>
        </div>
        <div className="button-row">
          <button type="button" disabled={busy || !canSave} onClick={() => void save()}>
            {busy ? "Saving…" : data.available ? "Save focus" : "Start tracking it"}
          </button>
          {editing && <button type="button" className="ghost-button" onClick={() => setEditing(false)}>Cancel</button>}
          {data.available && <button type="button" className="ghost-button" disabled={busy} onClick={() => void remove()}>Remove focus</button>}
        </div>
      </article>
    );
  }

  if (!data.focus) return null;

  const pct = data.progress_pct ?? 0;
  const months = data.months ?? [];
  const scale = Math.max(1, ...months.map((m) => Math.abs(m.kept)),
    Math.abs(data.this_month?.kept_so_far ?? 0));
  const byDate = data.by_target_date;

  return (
    <article className="chart-card focus-card">
      <div className="chart-card-head">
        <div>
          <h3>{data.focus.name}</h3>
          <p className="chart-explainer">
            Funded by what you kept in each finished month since{" "}
            {data.focus.started_month}. Nothing here is typed in.
          </p>
        </div>
        <span className={`badge${data.reached ? "" : byDate?.available && !byDate.on_track ? " badge-warn" : ""}`}>
          {data.reached ? "Funded" : `${pct.toFixed(0)}%`}
        </span>
      </div>

      <div className="progress-track focus-track" aria-label={`${pct.toFixed(0)}% of target`}>
        <span style={{ width: `${pct}%` }} />
      </div>
      <p className="focus-sentence">{data.sentence}</p>

      <div className="nw-facts">
        <div><span>Put aside</span><strong>{money(data.saved ?? 0)}</strong>
          <small>of {money(data.target ?? 0)}</small></div>
        <div><span>Typical month</span>
          <strong className={(data.typical_month ?? 0) >= 0 ? "good-text" : ""}>
            {(data.typical_month ?? 0) >= 0 ? "+" : "−"}{money(Math.abs(data.typical_month ?? 0))}
          </strong>
          <small>across {data.months_counted} finished month{data.months_counted === 1 ? "" : "s"}</small></div>
        {data.this_month && <div><span>This month so far</span>
          <strong>{data.this_month.kept_so_far >= 0 ? "+" : "−"}{money(Math.abs(data.this_month.kept_so_far))}</strong>
          <small>{data.this_month.month} is not finished, so it is not counted yet</small></div>}
        {byDate?.available && !byDate.past_due && <div>
          <span>To hit {data.focus.target_date}</span>
          <strong className={byDate.on_track ? "good-text" : ""}>{money(byDate.needed_monthly ?? 0)}</strong>
          <small>a month for {byDate.months_left} more month{byDate.months_left === 1 ? "" : "s"}</small></div>}
      </div>

      {months.length > 0 && (
        <div className="focus-months">
          {months.map((m) => {
            const up = m.kept >= 0;
            return (
              <div className="focus-month" key={m.month} title={`${m.month}: kept ${moneyCents(m.kept)}, running total ${moneyCents(m.running)}`}>
                <div className="focus-month-track">
                  <i style={{
                    height: `${Math.max(3, (Math.abs(m.kept) / scale) * 100)}%`,
                    background: up ? VIZ.income : VIZ.spending,
                  }} />
                </div>
                <small>{m.month.slice(5)}</small>
              </div>
            );
          })}
          {data.this_month && (
            <div className="focus-month focus-month-partial" title={`${data.this_month.month} is still in progress`}>
              <div className="focus-month-track">
                <i style={{
                  height: `${Math.max(3, (Math.abs(data.this_month.kept_so_far) / scale) * 100)}%`,
                  background: data.this_month.kept_so_far >= 0 ? VIZ.income : VIZ.spending,
                  opacity: 0.5,
                }} />
              </div>
              <small>{data.this_month.month.slice(5)}*</small>
            </div>
          )}
        </div>
      )}
      {months.length > 0 && (
        <p className="file-meta">
          Each bar is one month&apos;s kept amount.
          {data.best_month && ` Best was ${data.best_month.month} at ${money(data.best_month.kept)}`}
          {data.weakest_month && data.best_month
            && data.weakest_month.month !== data.best_month.month
            && `, weakest ${data.weakest_month.month} at ${money(data.weakest_month.kept)}`}
          {data.best_month && "."}
          {data.this_month && " * still in progress."}
        </p>
      )}

      {byDate?.available && !byDate.on_track && !byDate.past_due && (
        <p className="guidance focus-gap">
          Closing that {money(byDate.shortfall_monthly ?? 0)} a month, or moving
          the date out by{" "}
          {Math.max(1, (data.projection?.months_to_go ?? 0) - (byDate.months_left ?? 0))}{" "}
          month{Math.max(1, (data.projection?.months_to_go ?? 0) - (byDate.months_left ?? 0)) === 1 ? "" : "s"},
          are the two honest ways to make this true.
        </p>
      )}
      {!data.projection?.available && data.projection?.reason && (
        <p className="guidance">{data.projection.reason}</p>
      )}

      <div className="button-row">
        <button type="button" className="ghost-button" onClick={startEditing}>Edit focus</button>
      </div>
      {error && <div className="inline-error">{error}</div>}
    </article>
  );
}
