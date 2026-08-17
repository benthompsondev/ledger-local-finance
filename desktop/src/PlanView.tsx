/**
 * Plan: what this month looks like, and how much room it leaves.
 *
 * The screen this replaces asked eleven things at once — a queue of review
 * items, optional cash checks, an income confirmation card, a commitment
 * drift comparison, a savings focus, preset stances, a 90-day outlook, and
 * somewhere among them the actual plan. Real use showed the obvious result:
 * saving the plan cleared one row of a list that stayed full, so a save that
 * had genuinely persisted was indistinguishable from one that had not.
 *
 * It is now one card. Four figures come in, one figure comes out, and one
 * button saves it. Income sources, recurring costs and account balances are
 * reviewed where they live rather than shouting from here.
 *
 * Every figure still comes from the engine. React arranges, focuses and
 * formats; it does no arithmetic.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { loadPlan, previewPlan, savePlan } from "./api";
import { money, moneyCents } from "./money";
import { createRequestGate, previewSignature } from "./planPreview";
import { readSavingsPreference } from "./preferences";
import type { PlanEquation, PlanPayload } from "./types";

interface Props {
  refreshToken: number;
  onDataChanged: () => void;
  onNavigate: (screen: string) => void;
}
type PlanForm = {
  mode: string; income: string; fixed: string; savings: string;
  buffer: string; overrideReason: string;
};
const emptyForm: PlanForm = {
  mode: "normal", income: "", fixed: "", savings: "", buffer: "",
  overrideReason: "",
};
const shown = (value: unknown) => Number(value ?? 0).toFixed(2);

const MONTH_NAME = (month: string) => {
  const [y, m] = month.split("-").map(Number);
  if (!y || !m) return month;
  return new Date(y, m - 1, 1).toLocaleString("en-CA", { month: "long" });
};

/** "2026-08-17 09:15:00" or an ISO stamp → "17 Aug". */
const savedOn = (raw?: string) => {
  const text = String(raw ?? "").trim();
  if (!text) return "";
  const parsed = new Date(text.endsWith("Z") ? text : text.replace(" ", "T"));
  if (Number.isNaN(parsed.getTime())) return "";
  return parsed.toLocaleDateString("en-CA", { day: "numeric", month: "short" });
};

function formFrom(payload: PlanPayload): PlanForm {
  const p = payload.working_plan;
  return {
    mode: p.mode || "normal",
    income: shown(p.income_target),
    fixed: shown(p.fixed_obligations ?? payload.fixed_obligations_suggestion),
    savings: shown(p.savings_target), buffer: shown(p.safety_buffer),
    overrideReason: p.fixed_override_reason ?? "",
  };
}

function PlanView({ refreshToken, onDataChanged, onNavigate }: Props) {
  const [data, setData] = useState<PlanPayload | null>(null);
  const [form, setForm] = useState<PlanForm>(emptyForm);
  const [equation, setEquation] = useState<PlanEquation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [justSaved, setJustSaved] = useState(false);
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
        const preference = readSavingsPreference();
        token = gate.current.begin();
        const preview = await previewPlan({
          mode: next.mode,
          fixedObligations: Number(next.fixed), savingsTarget: Number(next.savings),
          safetyBuffer: Number(next.buffer), applyPreset: false,
          applyPreference: true, savingsPreferenceStyle: preference.style,
          savingsPreferenceValue: preference.value,
        });
        if (!gate.current.isLatest(token)) return;
        next = { ...next, savings: shown(preview.values.savings_target),
          buffer: shown(preview.values.safety_buffer) };
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

  useEffect(() => {
    // An explicit action owns the form until it finishes. Starting a live
    // preview behind a save would invalidate that action's response and
    // could leave the screen looking busy forever.
    if (!data || busy) return;
    const signature = previewSignature(form);
    if (signature === previewed.current) return;
    // Any edit means the figures on screen are no longer the saved ones.
    setJustSaved(false);
    previewTimer.current = window.setTimeout(() => {
      previewTimer.current = null;
      previewed.current = signature;
      const token = gate.current.begin();
      void previewPlan({
        mode: form.mode,
        fixedObligations: Number(form.fixed), savingsTarget: Number(form.savings),
        safetyBuffer: Number(form.buffer), applyPreset: false,
      }).then(p => {
        if (!gate.current.isLatest(token)) return;
        setEquation(p.equation);
        setError("");
      }).catch(c => {
        if (!gate.current.isLatest(token)) return;
        setError(c instanceof Error ? c.message : String(c));
      });
    }, 180);
    return cancelPendingPreview;
  }, [data, form.income, form.fixed, form.savings, form.buffer, form.mode, busy]);

  const save = async () => {
    if (!data || !equation) return;
    cancelPendingPreview();
    const token = gate.current.begin();
    setBusy(true); setError(""); setJustSaved(false);
    try {
      // The equation on screen may be one debounce behind the form, so
      // re-derive it from the exact values being saved.
      const currentPreview = await previewPlan({
        mode: form.mode,
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
        month: data.month, mode: form.mode,
        fixedObligations: Number(form.fixed),
        savingsTarget: Number(form.savings), safetyBuffer: Number(form.buffer),
        notes: data.saved?.notes ?? "", fixedOverrideReason: form.overrideReason,
      });
      if (!gate.current.isLatest(token)) return;
      // Everything below is re-read from the saved payload, never from the
      // values that were submitted. A save is only shown as saved once the
      // engine has handed the stored row back.
      const savedForm = formFrom(payload);
      previewed.current = previewSignature(savedForm);
      setData(payload); setForm(savedForm); setEquation(payload.equation);
      setJustSaved(true);
      onDataChanged();
    } catch (c) {
      if (gate.current.isLatest(token)) {
        setError(c instanceof Error ? c.message : String(c));
      }
    } finally {
      // Unconditional on purpose. A background refresh can supersede this
      // token, and gating the reset behind isLatest would leave the button
      // reading "Saving…" with nothing left to finish it.
      setBusy(false);
    }
  };

  if (!data && !error) return (
    <section className="loading-panel"><span className="loading-dot" />Loading your plan…</section>
  );

  const monthName = data ? MONTH_NAME(data.month) : "";
  const overridden = !!data
    && Math.abs(Number(form.fixed) - data.fixed_obligations_suggestion) >= 0.01;
  const result = data?.last_plan_result;
  const notice = data?.plan_notice;
  const savedRecord = data?.saved;
  const savedStamp = savedOn(savedRecord?.updated_at);
  // "Saved" is read from the stored row, so it survives a reload and a
  // restart rather than living in this component's memory.
  // Only the three figures the user actually chooses, plus the reason.
  // Income is derived, so a moved baseline is not an unsaved edit of theirs.
  const decisions = (fixed: string, savings: string, buffer: string) =>
    [fixed, savings, buffer].join("|");
  const dirty = !!savedRecord && (
    decisions(form.fixed, form.savings, form.buffer)
      !== decisions(shown(savedRecord.fixed_obligations ?? 0),
        shown(savedRecord.savings_target), shown(savedRecord.safety_buffer))
    // The reason is persisted too, so changing only it is still a change.
    || form.overrideReason.trim() !== (savedRecord.fixed_override_reason ?? "").trim()
  );

  return (
    <section className="workflow-panel plan-panel">
      <div className="panel-head">
        <div>
          {/* The rows below name themselves, so a sentence restating them
              only pushed the decision further down the page. */}
          <span className="eyebrow">Plan</span>
          <h2>{monthName}</h2>
        </div>
      </div>
      {error && <div className="inline-error" role="alert">{error}</div>}

      {data && <>
        {notice && (
          <section className={`plan-notice plan-notice-${notice.level}`}>
            <div>
              <strong>{notice.headline}</strong>
              {notice.detail && <p>{notice.detail}</p>}
            </div>
            {notice.action && notice.screen && (
              <button type="button" className="ghost-button"
                onClick={() => onNavigate(notice.screen)}>{notice.action}</button>
            )}
          </section>
        )}

        <article className="plan-card">
          {/* Facts first. A derived figure is not an input and must not look
              like one — a read-only box reads as "a field you are somehow
              not allowed to use" rather than "a number Northstar worked
              out". These are labels with values. */}
          <dl className="plan-facts">
            <div className="plan-fact">
              <dt>Money coming in</dt>
              <dd>{moneyCents(equation?.income ?? 0)}</dd>
              <p>{data.income_basis.months_used
                ? data.income_basis.basis_label
                : "Import one complete month and Northstar can work this out."}</p>
            </div>
            {equation && equation.nonmonthly_reserves > 0 && (
              <div className="plan-fact">
                <dt>Set aside for non-monthly bills</dt>
                <dd>{moneyCents(equation.nonmonthly_reserves)}</dd>
                <p>Quarterly and annual bills, spread evenly.</p>
              </div>
            )}
          </dl>

          {/* Then the three figures that are actually yours to choose. */}
          <div className="plan-choices">
            <div className="plan-choice">
              <label htmlFor="plan-fixed">Fixed commitments</label>
              <div className="plan-input">
                <span aria-hidden="true">$</span>
                <input id="plan-fixed" type="number" min="0" step="0.01"
                  inputMode="decimal" value={form.fixed} disabled={busy}
                  onChange={e => setForm({ ...form, fixed: e.target.value })} />
              </div>
              <p>{data.fixed_commitments.length} recognized bill{data.fixed_commitments.length === 1 ? "" : "s"} and subscription{data.fixed_commitments.length === 1 ? "" : "s"}</p>
            </div>
            <div className="plan-choice">
              <label htmlFor="plan-savings">Amount you want to keep</label>
              <div className="plan-input">
                <span aria-hidden="true">$</span>
                <input id="plan-savings" type="number" min="0" step="0.01"
                  inputMode="decimal" value={form.savings} disabled={busy}
                  onChange={e => setForm({ ...form, savings: e.target.value })} />
              </div>
            </div>
            <div className="plan-choice">
              <label htmlFor="plan-buffer">Safety buffer</label>
              <div className="plan-input">
                <span aria-hidden="true">$</span>
                <input id="plan-buffer" type="number" min="0" step="0.01"
                  inputMode="decimal" value={form.buffer} disabled={busy}
                  onChange={e => setForm({ ...form, buffer: e.target.value })} />
              </div>
              <p>Held back rather than spent</p>
            </div>
          </div>

          {/* The answer. One rule, then the largest thing on the screen. */}
          <div className={`plan-outcome${equation?.coherent ? "" : " plan-outcome-error"}`}>
            <span>Left for everyday spending</span>
            <strong>{equation?.coherent
              ? moneyCents(equation.flexible) : "Over plan"}</strong>
            {!equation?.coherent && <p>{equation?.explanation}</p>}
          </div>

          {overridden && (
            <label className="stacked-label">Why a different fixed-cost amount?
              <input maxLength={160} value={form.overrideReason} disabled={busy}
                onChange={e => setForm({ ...form, overrideReason: e.target.value })}
                placeholder="Short reason, for example: rent is changing next month" />
            </label>
          )}

          <div className="plan-save-row">
            <button type="button" className="plan-save"
              disabled={busy || !equation?.coherent || (!!savedRecord && !dirty)}
              onClick={() => void save()}>
              {busy ? "Saving…"
                : savedRecord ? (dirty ? "Save changes" : "Saved")
                  : `Save ${monthName}'s plan`}
            </button>
            {/* The button already says Saved / Save changes, so this only
                adds what the button cannot: when, and whether anything is
                outstanding. No sentence explaining the button to itself. */}
            <span className="plan-save-state">
              {busy || justSaved ? ""
                : savedRecord && !dirty ? `Saved ${savedStamp || "this month"}`
                  : savedRecord ? "Unsaved changes"
                    : ""}
            </span>
          </div>
        </article>

        {result?.available && (
          <section className="plan-last">
            <span className="eyebrow">Last finished month · {result.month_label}</span>
            <div className="plan-last-figures">
              <div><span>Planned to keep</span>
                <strong>{money(result.intended_kept ?? 0)}</strong></div>
              <div><span>{result.kept_is_negative ? "Went behind by" : "Actually kept"}</span>
                <strong>{money(result.actual_kept_abs ?? 0)}</strong></div>
              <div><span>Difference</span>
                <strong className={result.met ? "good-text" : ""}>
                  {money(result.difference_abs ?? 0)} {result.met ? "more" : "less"}
                </strong></div>
            </div>
          </section>
        )}

        <details className="chart-card settings-collapse plan-detail">
          <summary>Where these numbers come from</summary>
          {data.income_basis.months?.length ? <>
            <strong className="detail-heading">Income by month</strong>
            {data.income_basis.months.map(m => (
              <div className="source-row" key={m.month}>
                <span>{m.month}</span><strong>{moneyCents(m.income)}</strong>
              </div>
            ))}
          </> : null}
          <strong className="detail-heading">Recognized fixed commitments</strong>
          {data.fixed_commitments.length ? data.fixed_commitments.map(item => (
            <div className="source-row" key={`${item.merchant}-${item.category}`}>
              <span>{item.merchant}<small>{item.kind} · {item.category}{item.is_shared ? ` · ${item.user_share_pct}% share of ${moneyCents(item.household_amount)}` : ""}</small></span>
              <strong>{moneyCents(item.amount)}/mo</strong>
            </div>
          )) : <p className="guidance">No fixed recurring costs are confirmed yet.</p>}
          {data.nonmonthly_commitments.length > 0 && <>
            <strong className="detail-heading">Bills that do not arrive every month</strong>
            {data.nonmonthly_commitments.map(item => (
              <div className="source-row" key={`${item.merchant}-${item.cadence}`}>
                <span>{item.merchant}<small>{item.note}</small></span>
                <strong>{moneyCents(item.monthly_setaside)}/mo</strong>
              </div>
            ))}
          </>}
          <p className="guidance">
            Recurring costs and income sources are reviewed in{" "}
            <button type="button" className="link-button"
              onClick={() => onNavigate("settings#recurring-costs")}>Settings</button>.
          </p>
        </details>
      </>}
    </section>
  );
}
export default PlanView;
