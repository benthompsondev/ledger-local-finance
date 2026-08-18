import { useCallback, useEffect, useState } from "react";
import { AnalysisContextNote } from "./AnalysisContextNote";
import {
  loadHomeDashboard, loadHomeSummary, loadInsightFeed, loadUpcomingMoney,
} from "./api";
import { MoneyRadar } from "./MoneyRadar";
import {
  CashflowChart, CategoryBars, CategoryDonut, IncomeSourceDonut,
  IncomeSteadiness, PaceChart, paceComparisonNote,
} from "./charts";
import { money, moneyCents } from "./money";
import {
  readAnalysisPeriod, readChartPeriod, saveAnalysisPeriod, saveChartPeriod,
  readHomeSecondary,
  type AnalysisPeriod, type ChartPeriod,
} from "./preferences";
import type {
  HomeDashboard, HomePacket, InsightFeed, TxPrefill, UpcomingMoney,
} from "./types";

interface Props {
  onAddData: () => void;
  onNavigate: (screen: string) => void;
  onDrill: (prefill: TxPrefill) => void;
  refreshToken: number;
}

function HomeView({ onAddData, onNavigate, onDrill, refreshToken }: Props) {
  const [packet, setPacket] = useState<HomePacket | null>(null);
  const [dashboard, setDashboard] = useState<HomeDashboard | null>(null);
  const [feed, setFeed] = useState<InsightFeed | null>(null);
  const [radar, setRadar] = useState<UpcomingMoney | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [chartPeriod, setChartPeriod] = useState<ChartPeriod>(readChartPeriod);
  const [analysisPeriod, setAnalysisPeriod] = useState<AnalysisPeriod>(readAnalysisPeriod);
  const showSecondary = readHomeSecondary();

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      // Each request starts a short-lived Python sidecar. On a brand-new data
      // directory both requests initialize SQLite, so starting them together
      // can race on the schema and surface "database is locked" on first run.
      // Load the authoritative summary first, then the chart-ready dashboard.
      const summary = await loadHomeSummary(analysisPeriod);
      const dash = await loadHomeDashboard(analysisPeriod);
      setPacket(summary);
      setDashboard(dash);
      // Additive: Home must still render if no pattern crosses a threshold or
      // a detector fails.
      try { setFeed(await loadInsightFeed()); } catch { setFeed(null); }
      // Guarded on purpose. The engine deliberately does not swallow a
      // radar failure, so a broken detector stays visible to the tests;
      // this is where it is kept harmless to the check-in.
      try { setRadar(await loadUpcomingMoney()); } catch { setRadar(null); }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }, [analysisPeriod]);

  const drillCategory = useCallback(
    (category: string) => {
      onDrill({
        category,
        cashflowRole: "spending",
        startDate: dashboard?.period_start ?? "",
        endDate: dashboard?.period_end ?? "",
      });
    },
    [dashboard, onDrill],
  );

  const drillMonthCategory = useCallback(
    (category: string) => {
      const month = dashboard?.category_pace.month;
      const item = dashboard?.category_pace.items.find((row) => row.category === category);
      onDrill({
        category,
        cashflowRole: "spending",
        startDate: month ? `${month}-01` : "",
        endDate: month
          ? `${month}-${String(dashboard?.category_pace.through_day ?? 1).padStart(2, "0")}`
          : "",
        // No comparison strip without something to compare against. The
        // previous month is null, not zero, when it was never imported far
        // enough to read.
        categoryComparison: item && item.previous != null && item.delta != null ? {
          category,
          current: item.amount,
          previous: item.previous,
          delta: item.delta,
          throughDay: dashboard?.category_pace.through_day ?? 0,
          previousMonth: dashboard?.category_pace.previous_month ?? "",
        } : undefined,
      });
    },
    [dashboard, onDrill],
  );

  const drillIncome = useCallback(
    (source: string) => onDrill({
      search: source,
      cashflowRole: "income",
      startDate: dashboard?.period_start ?? "",
      endDate: dashboard?.period_end ?? "",
    }),
    [dashboard, onDrill],
  );

  // Home gets the two strongest headlines. Insights gets the same findings
  // with their working and their transactions, so the split is "there is
  // something to look at" here and "here is what it was" there.
  const noticedCount = feed
    ? feed.concerns.length + (feed.positive ? 1 : 0)
      + (feed.also_noticed?.length ?? 0)
    : 0;
  const teasers = feed
    ? [...feed.concerns, ...(feed.positive ? [feed.positive] : [])].slice(0, 2)
    : [];

  const changeChartPeriod = (value: ChartPeriod) => {
    setChartPeriod(value);
    saveChartPeriod(value);
  };

  const changeAnalysisPeriod = (value: AnalysisPeriod) => {
    setAnalysisPeriod(value);
    saveAnalysisPeriod(value);
  };

  useEffect(() => {
    void refresh();
  }, [refresh, refreshToken]);

  return (
    <>
      {error && (
        <section className="error-panel" role="alert">
          <strong>SignalSpace could not load your Home summary.</strong>
          <span>{error}</span>
          <button type="button" onClick={() => void refresh()}>
            Try again
          </button>
        </section>
      )}

      {!packet && !error && (
        <section className="loading-panel">
          <span className="loading-dot" />
          Preparing your latest financial summary…
        </section>
      )}

      {packet && (
        <>
          <section className={`freshness freshness-${packet.freshness.state}`}>
            <div>
              <span className="eyebrow">Data freshness</span>
              <strong>{packet.freshness.headline}</strong>
            </div>
            <p>{packet.freshness.detail}</p>
            {packet.freshness.state !== "current" && (
              <button type="button" onClick={onAddData}>
                Add data
              </button>
            )}
          </section>
          <AnalysisContextNote analysis={packet.analysis} />
          {!!packet.freshness.accounts?.length && (
            <details className="coverage-details">
              <summary>
                {packet.freshness.accounts.length} account{packet.freshness.accounts.length === 1 ? "" : "s"} included
                <span>See account activity</span>
              </summary>
              {packet.freshness.account_activity_note && (
                <p className="guidance">{packet.freshness.account_activity_note}</p>
              )}
              {packet.freshness.accounts.map((account) => (
                <div className="rank-row" key={`${account.account_name}-${account.account_type}`}>
                  <span>{account.account_name}<small>{account.account_type.replaceAll("_", " ")} · {account.tx_count} transactions</small></span>
                  <strong>last transaction {account.latest_date}</strong>
                </div>
              ))}
            </details>
          )}

          <section className={`verdict verdict-${packet.verdict.state}`}>
            <span className="eyebrow">Your current check-in</span>
            <h2>{packet.verdict.headline}</h2>
            <p>{packet.verdict.sentence}</p>
            {!!packet.verdict.setup_steps?.length && <div className="button-row">{packet.verdict.setup_steps.map(step=><button type="button" key={step.screen} onClick={()=>onNavigate(step.screen)}>{step.label}</button>)}</div>}
          </section>

          <div className="analysis-period-bar">
            <div>
              <strong>Your financial snapshot</strong>
              <span>{packet.period_start} to {packet.period_end}</span>
            </div>
            <label className="period-control">
              Period
              <select value={analysisPeriod} onChange={(event) => changeAnalysisPeriod(Number(event.target.value) as AnalysisPeriod)}>
                <option value={30}>Last 30 days</option>
                <option value={90}>Last 90 days</option>
              </select>
            </label>
          </div>

          <section className="summary-grid">
            <button type="button" className={`metric-card metric-card-action metric-primary ${packet.period_result.positive?"":"metric-negative"}`} onClick={()=>onDrill({cashflowRole:"net",startDate:packet.period_start,endDate:packet.period_end})}>
              <span className="eyebrow">{packet.period_result.label} · {packet.period_label}</span>
              <strong>{money(packet.period_result.amount)}</strong>
              <p>{packet.period_result.sentence}</p>
              <small>{packet.period_result.comparison_available
                ? `${packet.period_result.comparison_direction === "improved" ? "Improved" : "Lower"} by ${money(packet.period_result.comparison_delta_abs)}. ${packet.period_result.comparison_label}.`
                : `${packet.period_result.comparison_label}; no comparison is shown.`}</small>
            </button>
            <button type="button" className="metric-card metric-card-action" onClick={()=>onDrill({cashflowRole:"income",startDate:packet.period_start,endDate:packet.period_end})}>
              <span className="eyebrow">Money In</span>
              <strong>{money(packet.progress.mtd_income)}</strong>
              <p>Money entering your accounts in {packet.period_label.toLowerCase()}, including income, refunds, and incoming e-transfers{packet.progress.shared_reimbursements?" and shared reimbursements":""}.</p>
            </button>
            <button type="button" className="metric-card metric-card-action" onClick={()=>onDrill({cashflowRole:"spending",startDate:packet.period_start,endDate:packet.period_end})}>
              <span className="eyebrow">Spending</span>
              <strong>{money(packet.progress.mtd_spending)}</strong>
              <p>Purchases, bills, and fees. Transfers and card payments stay excluded.</p>
            </button>
            {packet.progress.show_payroll_breakout && <button type="button" className="metric-card metric-card-action" onClick={()=>onDrill({category:"Payroll Income",startDate:packet.period_start,endDate:packet.period_end})}>
              <span className="eyebrow">Payroll received</span>
              <strong>{money(packet.progress.payroll_received??0)}</strong>
              <p>{money(packet.progress.non_payroll_inflow??0)} of Money In came from other sources, including refunds when present.</p>
            </button>}
          </section>

          {dashboard?.available && (
            <div className="insight-grid home-detail-grid home-essentials">
              <article className="chart-card">
                <h3>Spending pace in {dashboard.analysis.data_month_label || "the latest supported period"}</h3>
                <p className="chart-explainer">Comparisons only ever span days both months share, so a partial month is never measured against a full one.</p>
                <PaceChart pace={dashboard.pace} currentLabel={dashboard.analysis.data_month_label || "Latest supported period"} />
              </article>
              <article className="chart-card">
                <h3>Top categories in {dashboard.analysis.data_month_label || "the latest supported period"}</h3>
                <p className="chart-explainer">{paceComparisonNote(dashboard.category_pace)}</p>
                <CategoryBars
                  items={dashboard.category_pace.items.slice(0, 4)}
                  total={dashboard.category_pace.total}
                  previousMonth={dashboard.category_pace.previous_month}
                  comparisonAvailable={dashboard.category_pace.comparison_available}
                  periodLabel={`${dashboard.analysis.data_month_label || "Latest supported period"} through day ${dashboard.category_pace.through_day}`}
                  onDrill={drillMonthCategory}
                />
              </article>
            </div>
          )}

          <article className="metric-card safe-secondary-card">
            <span className="eyebrow">{!packet.safe_to_spend.available ? "Safe to spend now" : packet.safe_to_spend.reserved.basis === "flow" ? "Estimated left for everyday spending" : "Safe to Spend · balance-checked"}</span>
            <strong>{packet.safe_to_spend.available ? money(packet.safe_to_spend.amount) : "Not available"}</strong>
            <p>{packet.safe_to_spend.available ? (packet.safe_to_spend.reserved.basis === "flow" ? `Not balance-checked · ${packet.safe_to_spend.reserved.income_basis_label||"estimated from your history"}` : `${packet.safe_to_spend.confidence} confidence · cash available now after confirmed reservations`) : packet.safe_to_spend.reason}</p>
            {!packet.safe_to_spend.available && packet.safe_to_spend.setup_screen && <button className="ghost-button" type="button" onClick={()=>onNavigate(packet.safe_to_spend.setup_screen!)}>Finish setup</button>}
            {packet.safe_to_spend.available && packet.safe_to_spend.reserved.basis === "flow" && <details className="formula-details" open><summary>Why this number?</summary><div className="formula-list"><span>Typical monthly income <strong>{money(packet.safe_to_spend.reserved.reliable_income||0)}</strong></span><span>− Recurring commitments <strong>−{money(packet.safe_to_spend.reserved.recurring_commitments||0)}</strong></span><span>− Savings target <strong>−{money(packet.safe_to_spend.reserved.savings_target||0)}</strong></span>{(packet.safe_to_spend.reserved.buffer||0)>0&&<span>− Safety buffer <strong>−{money(packet.safe_to_spend.reserved.buffer||0)}</strong></span>}<span className="formula-subtotal">Monthly everyday plan <strong>{money(packet.safe_to_spend.reserved.monthly_plan||0)}</strong></span><span>− Everyday spending so far this month <strong>−{money(packet.safe_to_spend.reserved.flexible_spent||0)}</strong></span><span className="formula-total">Left for everyday spending <strong>{money(packet.safe_to_spend.amount)}</strong></span></div><p className="guidance">Typical monthly income: {packet.safe_to_spend.reserved.income_basis_label||"median of your complete months"}. Recurring bills are already set aside, so they are not counted again in spending so far. Add account balances to check this against real cash.</p></details>}{packet.safe_to_spend.available && packet.safe_to_spend.reserved.basis !== "flow" && <details className="formula-details"><summary>Why this number?</summary><div className="formula-list"><span>Flexible allowance remaining <strong>{money(packet.safe_to_spend.reserved.flexible_remaining||0)}</strong></span><span>Cash cushion after bills <strong>{money(packet.safe_to_spend.reserved.cash_cushion_after_bills||0)}</strong></span><span className="formula-total">Safe now is the lower amount <strong>{money(packet.safe_to_spend.amount)}</strong></span></div><p className="guidance">Cash cushion uses current spendable balances minus complete card liabilities, unpaid confirmed bills, savings still to contribute, and the safety buffer. It is context, not extra spending permission.</p>{packet.safe_to_spend.reserved.reconciliation?.accounts?.map(account=><p className="guidance" key={`${account.account_ref}-${account.account_name}`}>{account.account_name}: balance as of {account.as_of_date||"not entered"}{account.last_activity?` · transactions through ${account.last_activity}`:""}.</p>)}{(packet.safe_to_spend.reserved.excluded_balance||0)>0&&<p className="guidance">{money(packet.safe_to_spend.reserved.excluded_balance||0)} in protected savings or other accounts is not included.</p>}{(packet.safe_to_spend.reserved.future_income_forecast||0)>0&&<p className="guidance">{money(packet.safe_to_spend.reserved.future_income_forecast||0)} of planned income has not arrived. It is forecast separately and is not included here.</p>}</details>}
          </article>

          {/* Directly under Safe to Spend: that number is about now, and
              this is the same question asked about the next few weeks.
              Everything below stays where it was. */}
          {radar && <MoneyRadar data={radar} onDrill={onDrill} />}

          {/* Teasers, not the experience. Insights has the same findings
              with their working, their evidence and a way into the
              transactions; repeating that here would mean the same three
              cards twice, and Home would stop being a check-in. Two lines,
              the headline and its figure, and a way through. */}
          {feed && teasers.length > 0 && <article className="chart-card">
            <div className="chart-card-head">
              <div>
                <h3>What SignalSpace noticed</h3>
                <p className="chart-explainer">
                  Worked out from your imported transactions.
                </p>
              </div>
              <button className="ghost-button" type="button"
                onClick={() => onNavigate("insights")}>
                {noticedCount > teasers.length
                  ? `See all ${noticedCount} in Insights`
                  : "See these in Insights"}
              </button>
            </div>
            <div className="insight-list">
              {teasers.map((insight) => (
                <article
                  className={`insight-card teaser${insight.tone === "good" ? " good" : ""}`}
                  key={insight.id}>
                  <h4>{insight.title}</h4>
                  {insight.figure != null && insight.figure_kind !== "" && (
                    <p className="teaser-figure">
                      <strong>
                        {insight.figure_kind === "count"
                          ? String(insight.figure)
                          : moneyCents(Math.abs(insight.figure))}
                      </strong>
                      <span>{insight.figure_caption}</span>
                    </p>
                  )}
                </article>
              ))}
            </div>
          </article>}

          <div className="home-actions-grid">
            <article className="chart-card">
              <h3>Recommended next actions</h3>
              {packet.actions.length ? packet.actions.slice(0,2).map(action => <div className="finding" key={action.id}><strong>{action.title}</strong><p>{action.why}</p>{action.label&&<button type="button" className="ghost-button finding-drill" onClick={()=>action.category?onDrill({category:action.category}):onNavigate(action.screen)}>{action.label}</button>}</div>) : <p className="guidance">Nothing urgent needs your attention for this check-in.</p>}
            </article>
          </div>

          {showSecondary && dashboard?.available && (
              <article className="chart-card">
                <div className="chart-card-head">
                  <div><h3>Cash flow by month</h3><p>Compare Money In with day-to-day spending.</p></div>
                  <label className="period-control">Show<select value={chartPeriod} onChange={(event) => changeChartPeriod(Number(event.target.value) as ChartPeriod)}><option value={3}>3 months</option><option value={6}>6 months</option><option value={12}>12 months</option></select></label>
                </div>
                <CashflowChart months={dashboard.cashflow.slice(-chartPeriod)} onDrill={(month,role)=>{const[y,m]=month.split("-").map(Number);onDrill({cashflowRole:role,startDate:`${month}-01`,endDate:`${month}-${new Date(y,m,0).getDate().toString().padStart(2,"0")}`});}} />
              </article>
          )}

          {showSecondary && dashboard && (dashboard.categories.available || dashboard.income_sources.length > 0) && (
            <div className="insight-grid breakdown-grid">
              {dashboard.categories.available && (
              <article className="chart-card">
                <h3>Spending by category</h3>
                <p className="chart-explainer">{dashboard.period_label}. Exact purchase amounts; refunds count as Money In. Transfers and card payments stay out.</p>
                <CategoryDonut
                  items={dashboard.categories.items}
                  total={dashboard.categories.total}
                  onDrill={drillCategory}
                />
                <CategoryBars
                  items={dashboard.categories.items.slice(0, 6)}
                  total={dashboard.categories.total}
                  previousMonth={dashboard.categories.previous_month}
                  comparisonAvailable={dashboard.categories.comparison_available}
                  periodLabel={dashboard.period_label}
                  onDrill={drillCategory}
                />
              </article>
              )}
              {dashboard.income_sources.length > 0 && (
                <article className="chart-card">
                  <h3>Income sources</h3>
                  <p className="chart-explainer">Where confirmed income came from during {dashboard.period_label.toLowerCase()}, including incoming e-transfers.</p>
                  <IncomeSourceDonut
                    items={dashboard.income_sources}
                    total={dashboard.income_total}
                    onDrill={drillIncome}
                  />
                  {/* The donut answers where it came from and then leaves half
                      a card empty. Whether it arrives reliably is the part
                      that changes a plan, and it is free from months already
                      loaded. */}
                  <IncomeSteadiness
                    months={dashboard.cashflow}
                    sources={dashboard.income_sources}
                    total={dashboard.income_total}
                  />
                </article>
              )}
            </div>
          )}

          {showSecondary && !!dashboard?.merchants.length && (
            <article className="chart-card">
              <h3>Top merchants · {dashboard.period_label}</h3>
              {dashboard.merchants.slice(0, 8).map((merchant) => (
                <button type="button" className="rank-row rank-row-click" key={`${merchant.merchant}-${merchant.category}`} onClick={() => onDrill({ search: merchant.merchant, cashflowRole: "spending", startDate: dashboard.period_start, endDate: dashboard.period_end })}>
                  <span>{merchant.merchant || "Unknown"}<small>{merchant.category} · {merchant.visits} visit{merchant.visits === 1 ? "" : "s"}</small></span>
                  <strong>{money(merchant.total)}</strong>
                </button>
              ))}
            </article>
          )}

          <footer className="local-note">
            <span className="status-light" />
            Everything is calculated locally. Optional AI connects only when you enable it.
            <button
              type="button"
              className="ghost-button"
              onClick={() => void refresh()}
              disabled={loading}
            >
              {loading ? "Loading…" : "Refresh"}
            </button>
          </footer>
        </>
      )}
    </>
  );
}

export default HomeView;
