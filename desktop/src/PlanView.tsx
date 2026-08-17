/**
 * Plan: one monthly equation, and one thing to do about it.
 *
 * The screen used to present eleven jobs at once — outlook, readiness,
 * income confirmation, commitment review, savings presets, five inputs,
 * an equation, non-monthly bills, price changes, Money Focus, and three
 * forecast cards — with no order of importance. It is now four sections:
 * where you stand, the plan itself, review and save, and everything else
 * folded away.
 *
 * Every figure still comes from the engine. React arranges, focuses and
 * formats; it does no arithmetic.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { loadPlan, previewPlan, savePlan, setIncomeSourcePreference } from "./api";
import MoneyFocusCard from "./MoneyFocusCard";
import { money, moneyCents } from "./money";
import { readSavingsPreference } from "./preferences";
import { createRequestGate, previewSignature } from "./planPreview";
import type { PlanEquation, PlanPayload } from "./types";

interface Props {
  refreshToken: number;
  onDataChanged: () => void;
  onNavigate: (screen: string) => void;
  focusAnchor?: string;
  focusToken?: number;
}
type PlanForm = {
  mode: string; income: string; fixed: string; savings: string;
  buffer: string; notes: string; overrideReason: string;
};
const emptyForm: PlanForm = {
  mode: "normal", income: "", fixed: "", savings: "", buffer: "",
  notes: "", overrideReason: "",
};
const shown = (value: unknown) => Number(value ?? 0).toFixed(2);

const MONTH_NAME = (month: string) => {
  const [y, m] = month.split("-").map(Number);
  if (!y || !m) return month;
  return new Date(y, m - 1, 1).toLocaleString("en-CA", { month: "long" });
};

function formFrom(payload: PlanPayload): PlanForm {
  const p = payload.working_plan;
  return {
    mode: p.mode || "normal",
    income: shown(p.income_target),
    fixed: shown(p.fixed_obligations ?? payload.fixed_obligations_suggestion),
    savings: shown(p.savings_target), buffer: shown(p.safety_buffer),
    notes: p.notes ?? "", overrideReason: p.fixed_override_reason ?? "",
  };
}

function PlanView({ refreshToken, onDataChanged, onNavigate, focusAnchor, focusToken }: Props) {
  const [data, setData] = useState<PlanPayload | null>(null);
  const [form, setForm] = useState<PlanForm>(emptyForm);
  const [equation, setEquation] = useState<PlanEquation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const reviewRef = useRef<HTMLElement | null>(null);
  const [highlight, setHighlight] = useState(false);
  // Only the newest preview may apply its result. Each request is its own
  // sidecar process, so out-of-order responses are ordinary, not exotic.
  const gate = useRef(createRequestGate());
  // The inputs the hydration preview already answered for. The debounced
  // effect below skips them, so opening an unsaved plan asks once, not twice.
  const previewed = useRef("");
  const previewTimer = useRef<number | null>(null);

  const cancelPendingPreview = () => {
    if (previewTimer.current === null) return;
    window.clearTimeout(previewTimer.current);
    previewTimer.current = null;
  };

  const refresh = useCallback(async () => {
    cancelPendingPreview();
    let token = gate.current.begin();
    setError("");
    try {
      const payload = await loadPlan();
      if (!gate.current.isLatest(token)) return;
      let next = formFrom(payload);
      setData(payload); setForm(next); setEquation(payload.equation);
      previewed.current = previewSignature(next);
      if (!payload.saved) {
        const pref = readSavingsPreference();
        token = gate.current.begin();
        const preview = await previewPlan({
          mode: next.mode, incomeTarget: Number(next.income),
          fixedObligations: Number(next.fixed), savingsTarget: Number(next.savings),
          safetyBuffer: Number(next.buffer), applyPreset: false,
          applyPreference: true, savingsPreferenceStyle: pref.style,
          savingsPreferenceValue: pref.value,
        });
        if (!gate.current.isLatest(token)) return;
        next = { ...next, savings: shown(preview.values.savings_target),
          buffer: shown(preview.values.safety_buffer) };
        // Record the hydrated values, not the pre-hydration ones: the
        // preference just changed savings and buffer, and re-previewing the
        // result of a preview answers nothing new.
        previewed.current = previewSignature(next);
        setForm(next); setEquation(preview.equation);
      }
    } catch (c) {
      if (gate.current.isLatest(token)) {
        setError(c instanceof Error ? c.message : String(c));
      }
    }
  }, []);
  useEffect(() => { void refresh(); }, [refresh, refreshToken]);

  // "Review changes" used to navigate to Plan while Plan was already open,
  // which did nothing at all. It now targets an anchor, and the section is
  // scrolled to, focused and briefly marked once the data has loaded.
  // Arriving here changes nothing about the plan; it only shows the user
  // the decision.
  useEffect(() => {
    if (focusAnchor !== "commitment-review" || !data) return;
    const timer = window.setTimeout(() => {
      const node = reviewRef.current;
      if (!node) return;
      node.scrollIntoView({ behavior: "smooth", block: "center" });
      node.focus({ preventScroll: true });
      setHighlight(true);
      window.setTimeout(() => setHighlight(false), 2200);
    }, 260);
    return () => window.clearTimeout(timer);
  }, [focusAnchor, focusToken, data]);

  useEffect(() => {
    // An explicit action owns the form until it finishes. Starting a live
    // preview behind a preset or save would invalidate that action's response
    // and could leave the screen looking busy forever.
    if (!data || busy) return;
    const signature = previewSignature(form);
    // Hydration already previewed exactly these inputs. Asking again would
    // be a second sidecar for an answer we are already showing.
    if (signature === previewed.current) return;
    previewTimer.current = window.setTimeout(() => {
      previewTimer.current = null;
      previewed.current = signature;
      const token = gate.current.begin();
      void previewPlan({
        mode: form.mode, incomeTarget: Number(form.income),
        fixedObligations: Number(form.fixed), savingsTarget: Number(form.savings),
        safetyBuffer: Number(form.buffer), applyPreset: false,
      }).then(p => {
        if (!gate.current.isLatest(token)) return;
        setEquation(p.equation);
        // A working preview means whatever went wrong before is over.
        setError("");
      }).catch(c => {
        // A stale failure must never replace a newer correct equation.
        if (!gate.current.isLatest(token)) return;
        setError(c instanceof Error ? c.message : String(c));
      });
    }, 180);
    return cancelPendingPreview;
  }, [data, form.income, form.fixed, form.savings, form.buffer, form.mode, busy]);

  const choosePreset = async (mode: string) => {
    cancelPendingPreview();
    const token = gate.current.begin();
    setBusy(true); setError("");
    try {
      const p = await previewPlan({
        mode, incomeTarget: Number(form.income), fixedObligations: Number(form.fixed),
        savingsTarget: Number(form.savings), safetyBuffer: Number(form.buffer),
        applyPreset: true,
      });
      if (!gate.current.isLatest(token)) return;
      const next = { ...form, mode, savings: shown(p.values.savings_target),
        buffer: shown(p.values.safety_buffer) };
      // This preview already answered for these values.
      previewed.current = previewSignature(next);
      setForm(next);
      setEquation(p.equation);
      setError("");
    } catch (c) {
      if (gate.current.isLatest(token)) {
        setError(c instanceof Error ? c.message : String(c));
      }
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!data || !equation) return;
    cancelPendingPreview();
    const token = gate.current.begin();
    setBusy(true); setError(""); setSaved("");
    try {
      // The equation shown on screen may be up to one debounce behind the
      // form. Re-preview the exact values being saved so flexible spending is
      // derived from the current inputs, not from the previous render.
      const currentPreview = await previewPlan({
        mode: form.mode, incomeTarget: Number(form.income),
        fixedObligations: Number(form.fixed), savingsTarget: Number(form.savings),
        safetyBuffer: Number(form.buffer), applyPreset: false,
      });
      if (!gate.current.isLatest(token)) return;
      setEquation(currentPreview.equation);
      if (!currentPreview.equation.coherent) {
        setError(currentPreview.equation.explanation);
        return;
      }
      const payload = await savePlan({
        month: data.month, mode: form.mode, incomeTarget: Number(form.income),
        fixedObligations: Number(form.fixed),
        flexibleAllowance: currentPreview.equation.flexible,
        savingsTarget: Number(form.savings), safetyBuffer: Number(form.buffer),
        notes: form.notes, fixedOverrideReason: form.overrideReason,
      });
      if (!gate.current.isLatest(token)) return;
      const savedForm = formFrom(payload);
      // The saved payload carries its own equation; previewing it again
      // would be a sidecar asking a question that just came back answered.
      previewed.current = previewSignature(savedForm);
      setData(payload); setForm(savedForm); setEquation(payload.equation);
      setSaved(`Saved. ${payload.readiness.plan_agreement.needs_review
        ? "Fixed costs still differ from what was reviewed."
        : "Fixed costs now match what Northstar reviewed."}`);
      onDataChanged();
    } catch (c) {
      if (gate.current.isLatest(token)) {
        setError(c instanceof Error ? c.message : String(c));
      }
    } finally {
      setBusy(false);
    }
  };

  const confirmIncome = async (source: string) => {
    cancelPendingPreview();
    gate.current.begin();
    setBusy(true); setError("");
    try { await setIncomeSourcePreference(source, "confirmed"); await refresh(); onDataChanged(); }
    catch (c) { setError(c instanceof Error ? c.message : String(c)); }
    finally { setBusy(false); }
  };

  if (!data && !error) return (
    <section className="loading-panel"><span className="loading-dot" />Loading your plan…</section>
  );

  const drift = data?.readiness.plan_agreement;
  const overridden = !!data && Math.abs(Number(form.fixed) - data.fixed_obligations_suggestion) >= 0.01;
  const monthName = data ? MONTH_NAME(data.month) : "";
  const incomeConfirmed = !!data?.reliable_income.confirmed;

  return (
    <section className="workflow-panel">
      <div className="panel-head">
        <div>
          <span className="eyebrow">Plan</span>
          <h2>Your monthly money plan</h2>
          <p>What comes in, what is already committed, and what is left to spend.</p>
        </div>
      </div>
      {error && <div className="inline-error" role="alert">{error}</div>}
      {data && <>

        {/* ── Last month's result ────────────────────────────────────
            Absent unless the engine can prove an exact plan for a
            canonically complete month predated that month's end. No card,
            no nag, and no arithmetic here — every figure is supplied. */}
        {data.last_plan_result?.available && (
          <section className="plan-result-card">
            <span className="eyebrow">
              Last finished month · {data.last_plan_result.month_label}
            </span>
            <h3 className="plan-result-headline">
              {/* Whole dollars: this is a sentence, not a ledger line. The
                  cents live in the comparison below, where the two figures
                  have to reconcile with each other. */}
              {data.last_plan_result.target_is_zero
                ? <>You planned to keep nothing, and kept{" "}
                    <strong>{money(data.last_plan_result.actual_kept ?? 0)}</strong>.</>
                : <><strong className={data.last_plan_result.met ? "good-text" : ""}>
                      {money(data.last_plan_result.difference_abs ?? 0)}
                    </strong>{" "}
                    {data.last_plan_result.met ? "more" : "less"} than you planned
                    to keep.</>}
            </h3>

            <div className="plan-result-compare">
              <div>
                <span>You planned to keep</span>
                <strong>{moneyCents(data.last_plan_result.intended_kept ?? 0)}</strong>
              </div>
              <b aria-hidden="true">→</b>
              <div>
                <span>{data.last_plan_result.kept_is_negative
                  ? "You went behind by" : "You kept"}</span>
                <strong>
                  {moneyCents(Math.abs(data.last_plan_result.actual_kept ?? 0))}
                </strong>
              </div>
            </div>

            <p className="plan-result-support">
              {moneyCents(data.last_plan_result.money_in ?? 0)} came in,{" "}
              {moneyCents(data.last_plan_result.spending ?? 0)} went out.
            </p>
            <p className="file-meta">
              {data.last_plan_result.coverage_note}
              {data.last_plan_result.planned_on
                ? ` Plan saved ${data.last_plan_result.planned_on}.` : ""}
            </p>

            <div className="button-row">
              <button type="button" className="ghost-button"
                onClick={() => onNavigate("plan#commitment-review")}>
                Review {monthName}’s plan
              </button>
            </div>
          </section>
        )}

        {/* ── A. Where you stand ─────────────────────────────────── */}
        <section className={`verdict-card verdict-${data.outcome.state}`}>
          <span className="eyebrow">Current outlook</span>
          <h3>{data.outcome.headline}</h3>
          <p>{data.outcome.sentence}</p>
          {data.analysis?.has_data && (
            <p className="plan-data-through">{data.analysis.label}</p>
          )}
        </section>

        {data.readiness.missing.length > 0 && (
          <section className="plan-readiness">
            <div>
              <span className="eyebrow">Plan decisions to review</span>
              <strong>{data.readiness.missing.length} item{data.readiness.missing.length === 1 ? "" : "s"} left</strong>
            </div>
            <div className="readiness-list">
              {data.readiness.missing.map(item => (
                <div className="readiness-item" key={item.id}>
                  <span><strong>{item.label}</strong><small>{item.detail}</small></span>
                  <button className="ghost-button" onClick={() => onNavigate(item.screen)}>{item.action}</button>
                </div>
              ))}
            </div>
          </section>
        )}

        {data.readiness.missing.length === 0 && (
          <section className="plan-readiness plan-readiness-ready">
            <div>
              <span className="eyebrow">Plan status</span>
              <strong>Monthly plan reviewed</strong>
              <p>{data.readiness.cash_checked
                ? "The plan is also checked against current account balances."
                : "The monthly flow plan is ready. Current balances have not been checked yet."}</p>
            </div>
          </section>
        )}

        {!!data.readiness.advisories?.length && (
          <details className="chart-card settings-collapse plan-advisories">
            <summary>Optional cash accuracy checks: {data.readiness.advisories.length}</summary>
            <p className="guidance">These improve the cash-backed Safe to Spend check. They do not invalidate the monthly plan built from imported transactions.</p>
            <div className="readiness-list">
              {data.readiness.advisories.map(item => (
                <div className="readiness-item" key={item.id}>
                  <span><strong>{item.label}</strong><small>{item.detail}</small></span>
                  <button className="ghost-button" onClick={() => onNavigate(item.screen)}>{item.action}</button>
                </div>
              ))}
            </div>
          </details>
        )}

        {!!data.income_confirmation_candidates.length && (
          <section className="income-confirmation-card">
            <div><span className="eyebrow">Likely payroll found</span>
              <h3>Confirm before Northstar plans with it</h3></div>
            {data.income_confirmation_candidates.map(candidate => (
              <div className="readiness-item" key={candidate.source_normalized}>
                <span><strong>{candidate.source}</strong>
                  <small>{moneyCents(candidate.monthly_amount)}/month · {candidate.cadence}. {candidate.reason}</small></span>
                <button type="button" disabled={busy}
                  onClick={() => void confirmIncome(candidate.source_normalized)}>Confirm payroll</button>
              </div>
            ))}
          </section>
        )}

        {/* ── B. The plan itself ─────────────────────────────────── */}
        <h3 className="insight-section">Your monthly plan</h3>
        <div className="form-card">
          <div className="plan-source-row">
            <div>
              <span className="eyebrow">{incomeConfirmed ? "Confirmed recurring income" : "Typical monthly income"}</span>
              <strong>{data.income_basis.months_used ? moneyCents(data.income_basis.amount) : "Not enough history yet"}</strong>
              <p>{data.income_basis.months_used
                ? `Estimated from ${data.income_basis.months_used} completed month${data.income_basis.months_used === 1 ? "" : "s"}.`
                : "Import a full month to estimate this."}
                {!incomeConfirmed && data.income_basis.months_used ? " Not yet confirmed as recurring." : ""}</p>
            </div>
            <div>
              <span className="eyebrow">Reviewed fixed commitments</span>
              <strong>{moneyCents(data.fixed_obligations_suggestion)}</strong>
              <p>{data.fixed_commitments.length} recognized bill{data.fixed_commitments.length === 1 ? "" : "s"} and subscription{data.fixed_commitments.length === 1 ? "" : "s"}.</p>
            </div>
          </div>

          <div className="form-grid">
            <label>Current income baseline
              <input type="number" min="0" step="0.01" value={form.income} readOnly />
              <small>{data.income_basis.basis_label}. The same figure Home uses.</small>
            </label>
            <label>Fixed monthly commitments
              <input type="number" min="0" step="0.01" value={form.fixed}
                disabled={busy}
                onChange={e => setForm({ ...form, fixed: e.target.value })} />
              <small>Bills and subscriptions you expect to pay.</small>
            </label>
            <label>Monthly savings target
              <input type="number" min="0" step="0.01" value={form.savings}
                disabled={busy}
                onChange={e => setForm({ ...form, savings: e.target.value })} />
            </label>
            <label>Safety buffer
              <input type="number" min="0" step="0.01" value={form.buffer}
                disabled={busy}
                onChange={e => setForm({ ...form, buffer: e.target.value })} />
            </label>
          </div>

          {equation && (
            <div className={`plan-equation-card ${equation.coherent ? "" : "equation-error"}`}>
              <span>{moneyCents(equation.income)} typical income</span><b>−</b>
              <span>{moneyCents(equation.fixed)} fixed</span><b>−</b>
              <span>{moneyCents(equation.nonmonthly_reserves)} nonmonthly reserves</span><b>−</b>
              <span>{moneyCents(equation.savings)} savings</span><b>−</b>
              <span>{moneyCents(equation.buffer)} buffer</span><b>=</b>
              <strong>{equation.coherent ? moneyCents(equation.flexible) : "Over plan"} flexible</strong>
              <p>{equation.explanation}</p>
            </div>
          )}

          <section className="summary-grid summary-grid-three">
            <article className="metric-card metric-primary">
              <span className="eyebrow">Flexible spending plan</span>
              <strong>{equation?.coherent ? moneyCents(equation.flexible) : "Not available"}</strong>
              <p>One monthly number for everyday spending after fixed, reserves, savings, and buffer.</p>
            </article>
            <article className="metric-card">
              <span className="eyebrow">{data.safe_to_spend.available && data.safe_to_spend.reserved.basis === "flow" ? "Estimated left for everyday spending" : "Safe to spend now"}</span>
              <strong>{data.safe_to_spend.available ? moneyCents(data.safe_to_spend.amount) : "Not ready"}</strong>
              <p>{data.safe_to_spend.available
                ? (data.safe_to_spend.reserved.basis === "flow"
                  ? `Monthly plan ${moneyCents(data.safe_to_spend.reserved.monthly_plan || 0)} minus ${moneyCents(data.safe_to_spend.reserved.flexible_spent || 0)} spent so far. Not balance-checked.`
                  : `Lower of ${moneyCents(data.safe_to_spend.reserved.flexible_remaining || 0)} flexible room and ${moneyCents(data.safe_to_spend.reserved.cash_cushion_after_bills || 0)} cash cushion after bills.`)
                : data.safe_to_spend.reason}</p>
            </article>
            <article className="metric-card">
              <span className="eyebrow">Cash cushion after bills</span>
              <strong>{data.safe_to_spend.available ? moneyCents(data.safe_to_spend.reserved.cash_cushion_after_bills || 0) : "Not ready"}</strong>
              <p>Cash-backed room after card balances and reservations. Context, not extra spending permission.</p>
            </article>
          </section>
        </div>

        {/* ── C. Review and save ─────────────────────────────────── */}
        <h3 className="insight-section">Review and save</h3>
        <article
          className={`chart-card commitment-review${highlight ? " review-flash" : ""}`}
          id="commitment-review"
          ref={reviewRef}
          tabIndex={-1}
          aria-label="Commitment review"
        >
          <h3>Fixed commitments</h3>
          {data.saved && drift?.needs_review ? <>
            <p className="chart-explainer">{drift.reason}</p>
            <div className="drift-compare">
              <div><span>Saved in your plan</span><strong>{moneyCents(drift.saved)}</strong></div>
              <div><span>Reviewed now</span><strong>{moneyCents(drift.detected)}</strong></div>
              <div><span>Difference</span>
                <strong className={drift.detected > drift.saved ? "pace-up" : "pace-down"}>
                  {drift.detected > drift.saved ? "▲" : "▼"} {moneyCents(Math.abs(drift.detected - drift.saved))}
                </strong></div>
            </div>
            {/* A total-level comparison, because the database does not store
                enough prior state to prove which merchant moved. Claiming an
                item-level diff would be inventing it. */}
            <p className="guidance">
              This compares totals. The recognized bills behind the reviewed
              figure are listed below.
            </p>
            <div className="button-row">
              <button type="button" disabled={busy}
                onClick={() => setForm({ ...form, fixed: shown(data.fixed_obligations_suggestion), overrideReason: "" })}>
                Use reviewed commitments
              </button>
            </div>
          </> : (
            <p className="guidance">
              {data.saved
                ? "Your saved plan matches the commitments Northstar has reviewed."
                : "Nothing saved yet. Confirm the plan below to start tracking against it."}
            </p>
          )}

          {overridden && (
            <label className="stacked-label">Why use a different fixed-cost amount?
              <input maxLength={160} value={form.overrideReason}
                disabled={busy}
                onChange={e => setForm({ ...form, overrideReason: e.target.value })}
                placeholder="Short reason, for example: rent is changing next month" />
            </label>
          )}
          <label className="stacked-label">Note for {monthName} (optional)
            <input value={form.notes} disabled={busy}
              onChange={e => setForm({ ...form, notes: e.target.value })} />
          </label>
          <div className="button-row">
            <button type="button" disabled={busy || !equation?.coherent} onClick={() => void save()}>
              {busy ? "Saving…" : `Use this plan for ${monthName}`}
            </button>
          </div>
          {saved && <p className="success-text">{saved}</p>}
        </article>

        <h3 className="insight-section">What you are keeping it for</h3>
        <MoneyFocusCard refreshToken={refreshToken} />

        {/* ── D. Everything else ─────────────────────────────────── */}
        <h3 className="insight-section">More detail</h3>
        <details className="chart-card settings-collapse">
          <summary>Starting points — {data.modes.find(m => m.value === form.mode)?.label ?? "Everyday"}</summary>
          <p className="guidance">A quick way to set savings and buffer. Both stay editable above.</p>
          <div className="preset-row" aria-label="Monthly plan presets">
            {data.modes.map(m => (
              <button type="button" key={m.value}
                className={form.mode === m.value ? "preset-button preset-active" : "preset-button"}
                disabled={busy}
                onClick={() => void choosePreset(m.value)}>
                <strong>{m.label}</strong><small>{m.description}</small>
              </button>
            ))}
          </div>
        </details>

        <details className="chart-card settings-collapse">
          <summary>Where these numbers come from</summary>
          {data.income_basis.months?.length ? <>
            <strong className="detail-heading">Income by month</strong>
            {data.income_basis.months.map(m => (
              <div className="source-row" key={m.month}><span>{m.month}</span><strong>{moneyCents(m.income)}</strong></div>
            ))}
          </> : null}
          <strong className="detail-heading">Recognized fixed commitments</strong>
          {data.fixed_commitments.length ? data.fixed_commitments.map(item => (
            <div className="source-row" key={`${item.merchant}-${item.category}`}>
              <span>{item.merchant}<small>{item.kind} · {item.category}{item.is_shared ? ` · ${item.user_share_pct}% share of ${moneyCents(item.household_amount)}` : ""}</small></span>
              <strong>{moneyCents(item.amount)}/mo</strong>
            </div>
          )) : <p className="guidance">No fixed recurring costs are confirmed yet.</p>}
          {data.readiness.review?.complete && (
            <p className="guidance">Income and recurring commitments reviewed{data.readiness.review.last_reviewed ? ` on ${data.readiness.review.last_reviewed.slice(0, 10)}` : ""}. <button className="link-button" onClick={() => onNavigate("settings#income-sources")}>Manage</button></p>
          )}
        </details>

        {data.nonmonthly_commitments.length > 0 && (
          <details className="chart-card settings-collapse">
            <summary>Bills that do not arrive every month — {moneyCents(data.nonmonthly_monthly_reserve)}/mo set aside</summary>
            <p className="guidance">{moneyCents(data.nonmonthly_annual_total)} a year. Setting aside {moneyCents(data.nonmonthly_monthly_reserve)} each month covers them, so the bill is already paid for when it lands.</p>
            {data.nonmonthly_commitments.map(item => (
              <div className="source-row" key={`${item.merchant}-${item.cadence}`}>
                <span>{item.merchant}<small>{item.note}{item.confidence === "low" ? " · based on only two payments so far" : ""}</small></span>
                <strong>{moneyCents(item.monthly_setaside)}/mo</strong>
              </div>
            ))}
          </details>
        )}

        {data.price_changes.length > 0 && (
          <details className="chart-card settings-collapse">
            <summary>Prices that changed — {data.price_changes.length}</summary>
            {data.price_changes.map(change => (
              <div className="source-row" key={change.merchant}>
                <span>{change.merchant}<small>{moneyCents(change.previous_amount)} to {moneyCents(change.current_amount)} in {change.changed_on.slice(0, 7)}</small></span>
                <strong>{change.annual_impact > 0 ? "+" : ""}{moneyCents(change.annual_impact)}/yr</strong>
              </div>
            ))}
          </details>
        )}

        <details className="chart-card settings-collapse">
          <summary>Planned next 90 days</summary>
          <p className="guidance">Planning targets from the values above, not predicted account balances.</p>
          <div className="outlook-grid">
            {data.outlook.map(row => (
              <article className="outlook-card" key={row.month}>
                <h3>{row.label}</h3>
                <dl>
                  <div><dt>Typical income</dt><dd>{moneyCents(row.stable_income)}</dd></div>
                  <div><dt>Fixed commitments</dt><dd>{moneyCents(row.fixed)}</dd></div>
                  <div><dt>Planned savings</dt><dd>{moneyCents(row.savings)}</dd></div>
                  <div><dt>Safety buffer</dt><dd>{moneyCents(row.buffer)}</dd></div>
                  <div className="outlook-flex"><dt>Flexible room</dt><dd>{moneyCents(row.flexible)}</dd></div>
                </dl>
              </article>
            ))}
          </div>
        </details>
      </>}
    </section>
  );
}
export default PlanView;
