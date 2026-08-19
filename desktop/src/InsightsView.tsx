import { useCallback, useEffect, useState } from "react";
import { AnalysisContextNote } from "./AnalysisContextNote";
import {
  loadInsights, loadInsightFeed, loadNetWorthTrend, loadSpendingPatterns,
  deleteNetWorthEntry, setIncomeSourcePreference,
  setRecurringPreference,
} from "./api";
import { NoticedCard } from "./NoticedCard";
import {
  CashflowChart, CategoryBars, CategoryDonut, DayOfWeekBars, IncomeSourceDonut,
  IncomeSteadiness, KeptChart, NetWorthTrendChart, PaceChart, paceComparisonNote,
  SpendingCalendar,
} from "./charts";
import { rollingAverageLabel } from "./insightComparison";
import { money, moneyCents } from "./money";
import {
  readAnalysisPeriod, readChartPeriod, readShowNetWorth, saveAnalysisPeriod, saveChartPeriod,
  readCompareBaseline, saveCompareBaseline, readCategoryExpanded, saveCategoryExpanded,
  type AnalysisPeriod, type ChartPeriod, type CompareBaseline,
} from "./preferences";
import { NetWorthEntryForm } from "./NetWorthEntryForm";
import { NetWorthFact } from "./NetWorthFact";
import { describeSpan } from "./netWorthFormat";
import type {
  Insight, InsightFeed, InsightsPayload, NetWorthOverview, SpendingPatterns,
  TxPrefill,
} from "./types";

interface Props {
  refreshToken: number;
  onDrill: (prefill: TxPrefill) => void;
  onNavigate: (screen: string) => void;
  onDataChanged: () => void;
}

function InsightsView({ refreshToken, onDrill, onNavigate, onDataChanged }: Props) {
  const [data, setData] = useState<InsightsPayload | null>(null);
  const [error, setError] = useState("");
  // Deleting a hand-typed reading is the only write this screen still makes.
  // Current balances moved to Settings, where they are described as planning
  // inputs rather than a second net worth.
  const [entryBusy, setEntryBusy] = useState(false);
  const [reviewBusy, setReviewBusy] = useState("");
  // Which income source is being reconsidered. A decided source shows the
  // decision; this is how you get the choice back.
  const [changingIncome, setChangingIncome] = useState("");
  const [paceView,setPaceView]=useState<"flexible"|"total">("flexible");
  const [chartPeriod, setChartPeriod] = useState<ChartPeriod>(readChartPeriod);
  const [analysisPeriod, setAnalysisPeriod] = useState<AnalysisPeriod>(readAnalysisPeriod);
  const [showAllCategories,setShowAllCategories]=useState(readCategoryExpanded);
  const [baseline,setBaseline]=useState<CompareBaseline>(readCompareBaseline);
  const [showNetWorth,setShowNetWorth]=useState(readShowNetWorth);
  const [showAllKept,setShowAllKept]=useState(false);
  const [patterns, setPatterns] = useState<SpendingPatterns | null>(null);
  const [feed, setFeed] = useState<InsightFeed | null>(null);
  const [trend, setTrend] = useState<NetWorthOverview | null>(null);
  const [editingMonth, setEditingMonth] = useState("");
  // A reading is typed by hand and cannot be recovered by importing a
  // statement again, so removing one asks first.
  const [confirmDelete, setConfirmDelete] = useState("");
  const refresh = useCallback(async () => {
    try {
      setData(await loadInsights(analysisPeriod));
      setError("");
      // Secondary, and never allowed to break the page: patterns are extra
      // colour on top of the figures, not the figures themselves.
      try { setPatterns(await loadSpendingPatterns(3)); } catch { setPatterns(null); }
      try { setFeed(await loadInsightFeed()); } catch { setFeed(null); }
      try { setTrend(await loadNetWorthTrend()); } catch { setTrend(null); }
    } catch (c) {
      setError(c instanceof Error ? c.message : String(c));
    }
  }, [analysisPeriod]);
  useEffect(() => {
    void refresh();
  }, [refresh, refreshToken]);

  if (!data && !error)
    return (
      <section className="loading-panel">
        <span className="loading-dot" />
        Building insights…
      </section>
    );

  const monthStart = data?.period_start ?? "";
  const monthEnd = data?.period_end ?? "";
  const insightMonthLabel = data?.analysis.data_month_label
    || "the latest supported period";
  const categoryStart = data?.category_pace.month
    ? `${data.category_pace.month}-01` : "";
  const categoryEnd = data?.category_pace.month
    ? `${data.category_pace.month}-${String(data.category_pace.through_day).padStart(2, "0")}` : "";
  // A plain proportional breakdown for the selected period. It has no
  // previous period behind it, so the baseline is null rather than zero: a
  // zero here would read as "nothing was spent on this last month".
  const periodCategoryItems = (data?.categories ?? []).map((item) => ({
    category: item.category,
    amount: item.total,
    share: item.pct,
    previous: null,
    delta: null,
  }));
  // Completeness is financial logic and belongs to the engine. Deciding it
  // here as "any month before the current one" is what made Insights and
  // Home disagree about which months counted.
  const cashflow = data?.monthly ?? [];
  // Three finished months by default. Six made the card a wall of bars
  // and buried the comparison it exists to show; the rest stay one
  // click away.
  const completeMonths = cashflow.filter((m) => m.complete);
  const savings = showAllKept ? completeMonths.slice(-12)
    : completeMonths.slice(-3);
  const reviewRecurring=async(merchant:string,status:string)=>{setReviewBusy(`recurring:${merchant}`);setError("");try{await setRecurringPreference(merchant,status);await refresh();onDataChanged();}catch(c){setError(c instanceof Error?c.message:String(c));}finally{setReviewBusy("");}};
  const reviewIncome=async(source:string,status:"confirmed"|"excluded")=>{setReviewBusy(`income:${source}`);setError("");try{await setIncomeSourcePreference(source,status);await refresh();onDataChanged();}catch(c){setError(c instanceof Error?c.message:String(c));}finally{setReviewBusy("");}};
  // The good-news card goes last: a page that opens with "well done" buries
  // the thing the person came to find out.
  const noticed: Insight[] = feed
    ? [...feed.concerns, ...(feed.positive ? [feed.positive] : [])]
    : [];
  const drillFinding = (drill: NonNullable<Insight["drill"]>) => onDrill({
    category: drill.category,
    search: drill.merchant,
    startDate: drill.start_date,
    endDate: drill.end_date,
    cashflowRole: "spending",
  });
  const removeEntry=async(month:string)=>{setEntryBusy(true);setError("");try{setTrend(await deleteNetWorthEntry(month));if(editingMonth===month)setEditingMonth("");setConfirmDelete("");}catch(c){setError(c instanceof Error?c.message:String(c));}finally{setEntryBusy(false);}};

  return (
    <section className="workflow-panel">
      <div className="panel-head">
        <div>
          <span className="eyebrow">Insights</span>
          <h2>Patterns from your local data</h2>
          <p>
            Clear trends and breakdowns from the transactions you imported.
          </p>
        </div>
        <div className="button-row">
          <label className="period-control">Period<select value={analysisPeriod} onChange={(event) => { const next = Number(event.target.value) as AnalysisPeriod; setAnalysisPeriod(next); saveAnalysisPeriod(next); }}><option value={30}>Last 30 days</option><option value={90}>Last 90 days</option></select></label>
          <button className="ghost-button" onClick={() => void refresh()}>Refresh</button>
        </div>
      </div>
      {error && <div className="inline-error">{error}</div>}
      {data && (
        <>
          <AnalysisContextNote analysis={data.analysis} />

          {/* The lead, because it is the one part of this page that finds
              something rather than drawing what you already asked for. The
              charts below are the evidence you go to next. */}
          {feed && <>
            <h3 className="insight-section">What SignalSpace noticed</h3>
            {noticed.length ? (
              <>
                <p className="chart-explainer noticed-intro">
                  Worked out from your imported transactions, ranked by what
                  they are worth a month. Nothing here is a projection.
                </p>
                <div className="noticed-grid">
                  {noticed.map((insight) => (
                    <NoticedCard key={insight.id} insight={insight}
                      onDrill={drillFinding} />
                  ))}
                </div>
                {(feed.also_noticed?.length ?? 0) > 0 && (
                  <details className="formula-details noticed-more">
                    <summary>
                      {feed.also_noticed!.length} more that did not make the top
                      three
                    </summary>
                    <div className="noticed-grid">
                      {feed.also_noticed!.map((insight) => (
                        <NoticedCard key={insight.id} insight={insight}
                          onDrill={drillFinding} />
                      ))}
                    </div>
                  </details>
                )}
              </>
            ) : (
              <p className="guidance">
                {feed.missing_data
                  ?? "Nothing in your history cleared the bar this time. A "
                     + "finding has to be worth real money and hold across "
                     + "enough finished months to be a pattern rather than a "
                     + "single costly week."}
              </p>
            )}
          </>}

          <h3 className="insight-section">Spending check-in</h3>
          <div className="insight-grid home-detail-grid home-essentials checkin-row">
            <article className="chart-card">
              <div className="chart-card-head"><div><h3>{paceView==="flexible"?"Flexible spending pace":"Total spending pace"} in {insightMonthLabel}</h3><p className="chart-explainer">{paceView==="flexible"?"Everyday spending with reviewed fixed commitments removed.":"All genuine spending, including fixed commitments."}</p></div><div className="segmented-control" aria-label="Spending pace view"><button className={paceView==="flexible"?"selected":""} onClick={()=>setPaceView("flexible")}>Flexible</button><button className={paceView==="total"?"selected":""} onClick={()=>setPaceView("total")}>Total</button></div></div>
              <PaceChart pace={paceView==="flexible"?data.flexible_pace:data.pace} currentLabel={insightMonthLabel} />
            </article>
            <article className="chart-card">
              <div className="chart-card-head">
                <div>
                  <h3>Where your money went in {insightMonthLabel} vs {baseline === "average" ? `your ${rollingAverageLabel(data.category_pace.average_month_count)}` : "the prior month"}</h3>
                  <p className="chart-explainer">{paceComparisonNote(data.category_pace)}</p>
                </div>
                {data.category_pace.has_average && (
                  <div className="segmented-control" aria-label="Comparison baseline">
                    <button className={baseline === "last" ? "selected" : ""} onClick={() => { setBaseline("last"); saveCompareBaseline("last"); }}>vs last month</button>
                    <button className={baseline === "average" ? "selected" : ""} onClick={() => { setBaseline("average"); saveCompareBaseline("average"); }}>vs {rollingAverageLabel(data.category_pace.average_month_count, true)}</button>
                  </div>
                )}
              </div>
              {/* Five, not eight. Eight rows at 67px each made this card
                  half again as tall as the pace chart beside it, and the
                  row was mostly the empty space that left. Five covers the
                  bulk of a month's spending and the rest is one click. */}
              <CategoryBars
                items={showAllCategories ? data.category_pace.items : data.category_pace.items.slice(0, 5)}
                total={data.category_pace.total}
                previousMonth={data.category_pace.previous_month}
                comparisonAvailable={data.category_pace.comparison_available}
                baseline={baseline}
                averageMonthCount={data.category_pace.average_month_count}
                periodLabel={`${insightMonthLabel} through day ${data.category_pace.through_day}`}
                  onDrill={(category) => {const item=data.category_pace.items.find(row=>row.category===category);onDrill({ category, cashflowRole: "spending", startDate: categoryStart, endDate: categoryEnd, categoryComparison:item&&item.previous!=null&&item.delta!=null?{category,current:item.amount,previous:item.previous,delta:item.delta,throughDay:data.category_pace.through_day,previousMonth:data.category_pace.previous_month}:undefined });}}
              />
              {data.category_pace.items.length > 5 && <button className="ghost-button show-all-button" onClick={() => { const next = !showAllCategories; setShowAllCategories(next); saveCategoryExpanded(next); }}>{showAllCategories ? "Show top 5" : `Show all ${data.category_pace.items.length} categories`}</button>}
              {data.category_pace.comparison_available && (data.category_movers.increase || data.category_movers.decrease) && (
                <p className="mover-summary">
                  <strong>Biggest movers:</strong>{" "}
                  {data.category_movers.increase?.delta != null && `${data.category_movers.increase.category} increased ${money(Math.abs(data.category_movers.increase.delta))}`}
                  {data.category_movers.increase && data.category_movers.decrease && " · "}
                  {data.category_movers.decrease?.delta != null && `${data.category_movers.decrease.category} decreased ${money(Math.abs(data.category_movers.decrease.delta))}`}.
                </p>
              )}
            </article>
          </div>

          {patterns && (patterns.calendar.available
            || patterns.day_of_week.available) && <>
            <h3 className="insight-section">Patterns</h3>
            <div className="insight-grid home-detail-grid">
              {patterns.calendar.available && <article className="chart-card">
                <div className="chart-card-head">
                  <div>
                    <h3>When you spend</h3>
                    <p className="chart-explainer">
                      Every day of the last three months. Darker is a bigger
                      day. Fixed bills are left out so the pattern shows the
                      spending you choose.
                    </p>
                  </div>
                </div>
                <SpendingCalendar calendar={patterns.calendar}
                  onDrill={(day) => onDrill({ startDate: day, endDate: day })} />
              </article>}
              {patterns.day_of_week.available && <article className="chart-card dow-card">
                <div className="chart-card-head">
                  <div>
                    <h3>Which days cost most</h3>
                    <p className="chart-explainer">
                      Average spending per weekday, counting how many of each
                      the window actually held.
                    </p>
                  </div>
                </div>
                <DayOfWeekBars profile={patterns.day_of_week} />
              </article>}
              {/* The year-ago comparison is deliberately not a card of its
                  own. "Where your money went, this month vs last" already
                  answers the same question with data everyone has, and a
                  seasonal comparison only earns space once there is a full,
                  well-covered year behind it. It returns when that is true. */}
            </div>
          </>}

          <h3 className="insight-section">Trends</h3>
          <div className="insight-grid home-detail-grid">
            <article className="chart-card">
              <div className="chart-card-head">
                <div><h3>Income and spending by month</h3><p>Transfers and card payments are excluded.</p></div>
                <label className="period-control">Show<select value={chartPeriod} onChange={(event) => { const next = Number(event.target.value) as ChartPeriod; setChartPeriod(next); saveChartPeriod(next); }}><option value={3}>3 months</option><option value={6}>6 months</option><option value={12}>12 months</option></select></label>
              </div>
              <CashflowChart months={cashflow.slice(-chartPeriod)} onDrill={(month,role)=>{const[y,m]=month.split("-").map(Number);onDrill({cashflowRole:role,startDate:`${month}-01`,endDate:`${month}-${new Date(y,m,0).getDate().toString().padStart(2,"0")}`});}} />
            </article>
            <article className="chart-card">
              <div className="chart-card-head">
                <div>
                  <h3>What you kept</h3>
                  <p className="chart-explainer">The same months as the chart beside this one, with what came in, what went out, and what was left of it.</p>
                </div>
                {completeMonths.length > 3 && (
                  <button className="ghost-button" type="button"
                    onClick={() => setShowAllKept(!showAllKept)}>
                    {showAllKept ? "Last 3 months" : "Show more"}
                  </button>
                )}
              </div>
              <KeptChart months={savings} onDrill={(month,role)=>{const[y,monthNumber]=month.split("-").map(Number);onDrill({cashflowRole:role,startDate:`${month}-01`,endDate:`${month}-${new Date(y,monthNumber,0).getDate().toString().padStart(2,"0")}`});}} />
            </article>
          </div>

          <h3 className="insight-section">Spending</h3>
          <div className="insight-grid home-detail-grid">
            <article className="chart-card">
              <h3>
                Categories — {data.period_label}
              </h3>
              {periodCategoryItems.length ? (
                <>
                  <CategoryDonut
                    items={periodCategoryItems}
                    total={data.cash_flow_summary.spending}
                    onDrill={(category) => onDrill({ category, cashflowRole: "spending", startDate: monthStart, endDate: monthEnd })}
                  />
                </>
              ) : (
                <p className="guidance">No spending in this period.</p>
              )}
            </article>
            <article className="chart-card content-card">
              {/* Six, to sit level with the donut beside it rather than
                  running 200px past the bottom of it. */}
              <h3>Top merchants — {data.period_label}</h3>
              {data.merchants.slice(0, 6).map((m) => (
                <button
                  type="button"
                  className="rank-row rank-row-click"
                  key={`${m.merchant}-${m.category}`}
                  onClick={() => onDrill({ search: m.merchant, cashflowRole: "spending", startDate: monthStart, endDate: monthEnd })}
                  title={`Open ${m.merchant || "these"} transactions`}
                >
                  <span>
                    {m.merchant || "Unknown"}
                    <small>
                      {m.category} · {m.visits} visit{m.visits === 1 ? "" : "s"}
                    </small>
                  </span>
                  <strong>{money(m.total)}</strong>
                </button>
              ))}
              {!data.merchants.length && (
                <p className="guidance">No merchant activity in this period.</p>
              )}
            </article>
          </div>

          <h3 className="insight-section">Income</h3>
          <div className="insight-grid home-detail-grid">
            <article className="chart-card">
              <h3>Income sources — {data.period_label}</h3>
              <p className="chart-explainer">Money entering your accounts, including refunds and incoming e-transfers. Transfers between your own accounts stay excluded once matched or marked internal.</p>
              {data.income_sources.length ? (
                <><IncomeSourceDonut items={data.income_sources} total={data.income_total} onDrill={(source) => onDrill({ search: source, cashflowRole: "income", startDate: monthStart, endDate: monthEnd })} />
                <div className="settings-list">{data.income_sources.map(source=>{
                  // Once the choice is made it is a fact, not a question.
                  // Two full-size buttons on every row for the rest of time
                  // is the screen still asking something already answered.
                  const key=source.source_normalized;
                  const decided=source.stable_status==="confirmed"||source.stable_status==="excluded";
                  const settled=decided&&changingIncome!==key;
                  return <div className="setting-row" key={`${key}-${source.category}`}>
                    <button type="button" className="rank-row-click text-button" onClick={()=>onDrill({search:source.source,cashflowRole:"income",startDate:monthStart,endDate:monthEnd})}>
                      <strong>{source.source}</strong>
                      <small>{moneyCents(source.total)} · {source.tx_count} deposit{source.tx_count===1?"":"s"}</small>
                    </button>
                    {settled ? <div className="income-decision">
                      <span className={source.stable_status==="confirmed"?"status-tag status-ok":"status-tag status-muted"}>
                        {source.stable_status==="confirmed"?"Counted as income":"Left out of planning"}
                      </span>
                      <button type="button" className="text-button decision-change" disabled={!!reviewBusy} onClick={()=>setChangingIncome(key)}>Change</button>
                    </div> : <div className="button-row compact-actions">
                      <button type="button" className={source.stable_status==="confirmed"?"selected ghost-button":"ghost-button"} disabled={!!reviewBusy} onClick={()=>{setChangingIncome("");void reviewIncome(key,"confirmed");}}>Use as income</button>
                      <button type="button" className={source.stable_status==="excluded"?"selected ghost-button":"ghost-button"} disabled={!!reviewBusy} onClick={()=>{setChangingIncome("");void reviewIncome(key,"excluded");}}>Exclude</button>
                      {decided&&<button type="button" className="text-button decision-change" onClick={()=>setChangingIncome("")}>Cancel</button>}
                    </div>}
                  </div>;
                })}</div>
                <p className="chart-explainer">Excluding a source takes it out of your typical monthly income, so Plan and Safe to Spend stop counting on it. Every month is recalculated, not just this one. Choosing it again puts it back.</p></>
              ) : <p className="guidance">No confirmed income in this period.</p>}
              <IncomeSteadiness months={cashflow} sources={data.income_sources} total={data.income_total} />
            </article>
            <article className="chart-card">
              <div className="chart-card-head"><h3>Recurring costs</h3><button className="ghost-button" onClick={()=>onNavigate("settings#recurring-costs")}>Manage</button></div>
              {data.recurring.length ? (
                data.recurring.slice(0, 8).map((r) => (
                  <div className="setting-row" key={r.merchant}>
                    <button type="button" className="text-button rank-row-click" onClick={() => onDrill({ search: r.merchant })} title={`Open ${r.merchant} transactions`}>
                      <span>
                      {r.merchant}
                      <small>
                        {r.cadence} · {r.category} · seen {r.months_seen} months
                      </small>
                      {r.is_shared&&<small>Your {r.user_share_pct}% share of {moneyCents(r.household_amount)}/mo</small>}
                      {r.expected_next&&<small>Expected around {r.expected_next}</small>}
                      </span>
                      <strong>{moneyCents(r.avg_amount)}/mo</strong>
                    </button>
                    {/* Two states, not three: a cost either recurs or it
                        does not. "Automatic" described how SignalSpace found
                        it, which is a detail, so it is a badge now. */}
                    <div className="button-row compact-actions">{r.recurring_status==="automatic"&&<span className="suggested-badge">Suggested</span>}<button type="button" className={r.recurring_status==="recurring"?"selected ghost-button":"ghost-button"} disabled={!!reviewBusy} onClick={()=>void reviewRecurring(r.merchant_normalized,"recurring")}>Recurring</button><button type="button" className={r.recurring_status==="not_recurring"?"selected ghost-button":"ghost-button"} disabled={!!reviewBusy} onClick={()=>void reviewRecurring(r.merchant_normalized,"not_recurring")}>Not recurring</button></div>
                  </div>
                ))
              ) : (
                <p className="guidance">
                  Recognized bills can appear after two consistent months; other merchants need at least three.
                </p>
              )}
            </article>
          </div>

          {showNetWorth&&<><h3 className="insight-section">Net worth</h3>
          <article className="chart-card wide-card">
            <div className="chart-card-head">
              <div>
                <h3>What you are worth, month by month</h3>
                <p className="chart-explainer">
                  Four figures once a month: cash, investments, other assets
                  and debts. Every point on the chart is a reading you
                  entered, so nothing here is projected.
                </p>
              </div>
              {trend?.has_entries && trend.since_last_reading && (
                <span className={`badge${trend.since_last_reading.amount >= 0 ? "" : " badge-warn"}`}>
                  {trend.since_last_reading.amount >= 0 ? "\u25b2" : "\u25bc"}{" "}
                  {money(Math.abs(trend.since_last_reading.amount))} since{" "}
                  {trend.since_last_reading.from_month}
                </span>
              )}
            </div>
            {trend?.has_entries ? <>
              <NetWorthTrendChart overview={trend} />
              <div className="nw-facts">
                <div><span>Now</span>
                  <strong>{money(trend.current?.net ?? 0)}</strong>
                  <small>recorded for {trend.current?.month}</small></div>
                <NetWorthFact
                  label="This month" move={trend.month_over_month ?? null}
                  missing="no reading for the previous month" />
                <NetWorthFact
                  label="Three months" move={trend.three_month ?? null}
                  missing="no reading three months back" />
                {trend.twelve_month && <NetWorthFact
                  label="Twelve months" move={trend.twelve_month} missing="" />}
                <div><span>All recorded</span>
                  <strong className={(trend.since_first?.amount ?? 0) >= 0 ? "good-text" : ""}>
                    {(trend.since_first?.amount ?? 0) >= 0 ? "+" : "\u2212"}
                    {money(Math.abs(trend.since_first?.amount ?? 0))}
                  </strong>
                  <small>
                    {trend.since_first
                      ? `${trend.since_first.reading_count} reading${trend.since_first.reading_count === 1 ? "" : "s"} spanning ${describeSpan(trend.since_first.months_apart)}, ${trend.since_first.from_month} to ${trend.since_first.to_month}`
                      : ""}
                  </small></div>
              </div>

              {trend.component_moves && trend.component_moves.some((m) => m.change !== 0) && (
                <div className="nw-components">
                  <h4>
                    What moved{trend.component_from_month
                      ? ` between ${trend.component_from_month} and ${trend.component_to_month}`
                      : ""}
                  </h4>
                  {(trend.component_months_apart ?? 1) > 1 && (
                    <p className="chart-explainer">
                      Those readings are {describeSpan(trend.component_months_apart ?? 0)} apart,
                      not one month.
                    </p>
                  )}
                  <table className="nw-table">
                    <thead><tr><th>Part</th><th>Before</th><th>Now</th><th>Change</th></tr></thead>
                    <tbody>
                      {trend.component_moves.map((m) => {
                        // A debt going down is good and a debt going up is
                        // not, which is the opposite of every other row here.
                        const better = m.field === "liabilities" ? m.change < 0 : m.change > 0;
                        return (
                          <tr key={m.field}>
                            <td>{m.label}</td>
                            <td>{money(m.before)}</td>
                            <td>{money(m.now)}</td>
                            <td className={m.change === 0 ? "" : better ? "good-text" : "warn-text"}>
                              {m.change === 0 ? "no change"
                                : `${m.change > 0 ? "+" : "\u2212"}${money(Math.abs(m.change))}`}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}

              {(trend.explained?.length ?? 0) > 0 && (
                <details className="formula-details">
                  <summary>How this compares with what your statements say</summary>
                  <p className="chart-explainer">
                    Your statements only see the accounts you import. The
                    difference is usually an investment account moving on its
                    own, which is exactly the part worth watching.
                  </p>
                  <table className="nw-table">
                    <thead><tr><th>Month</th><th>Net worth moved</th><th>Statements kept</th><th>Difference</th></tr></thead>
                    <tbody>
                      {trend.explained!.slice(-6).map((row) => (
                        <tr key={row.month}>
                          <td>{row.month}</td>
                          <td>{row.measured >= 0 ? "+" : "\u2212"}{money(Math.abs(row.measured))}</td>
                          <td>{row.kept >= 0 ? "+" : "\u2212"}{money(Math.abs(row.kept))}</td>
                          <td>{row.unexplained >= 0 ? "+" : "\u2212"}{money(Math.abs(row.unexplained))}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </details>
              )}

              <details className="formula-details" open={editingMonth !== ""}>
                <summary>Record or correct a month</summary>
                <NetWorthEntryForm
                  prefill={trend.prefill}
                  existing={trend.entries.find((e) => e.month === editingMonth)}
                  onSaved={(next) => { setTrend(next); setEditingMonth(""); }}
                  onCancel={editingMonth ? () => setEditingMonth("") : undefined}
                />
              </details>

              <details className="formula-details">
                <summary>Every reading ({trend.months_recorded})</summary>
                <table className="nw-table">
                  <thead><tr><th>Month</th><th>Cash</th><th>Investments</th><th>Other</th><th>Debts</th><th>Net worth</th><th>Change</th><th /></tr></thead>
                  <tbody>
                    {[...trend.entries].reverse().map((e) => (
                      <tr key={e.month}>
                        <td>{e.month}</td>
                        <td>{money(e.cash)}</td>
                        <td>{money(e.investments)}</td>
                        <td>{money(e.other_assets)}</td>
                        <td>{money(e.liabilities)}</td>
                        <td><strong>{money(e.net)}</strong></td>
                        <td className={e.change == null ? "" : e.change >= 0 ? "good-text" : "warn-text"}>
                          {e.change == null ? "first reading"
                            : `${e.change >= 0 ? "+" : "\u2212"}${money(Math.abs(e.change))}`}
                          {e.change_pct != null && <small> ({e.change_pct > 0 ? "+" : ""}{e.change_pct}%)</small>}
                        </td>
                        <td className="nw-row-actions">
                          {confirmDelete === e.month ? (
                            <>
                              <span className="nw-confirm">Remove {e.month}?</span>
                              <button type="button" className="danger-button"
                                disabled={entryBusy}
                                onClick={() => void removeEntry(e.month)}>Remove</button>
                              <button type="button" className="ghost-button"
                                onClick={() => setConfirmDelete("")}>Keep</button>
                            </>
                          ) : (
                            <>
                              <button type="button" className="ghost-button"
                                onClick={() => { setEditingMonth(e.month); setConfirmDelete(""); }}>Edit</button>
                              <button type="button" className="ghost-button"
                                onClick={() => setConfirmDelete(e.month)}>Delete</button>
                            </>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </details>
            </> : <>
              <p className="guidance">{trend?.reason ?? "Loading\u2026"}</p>
              {trend && <NetWorthEntryForm prefill={trend.prefill}
                onSaved={(next) => setTrend(next)} />}
              {trend?.prefill.has_balances && (
                <p className="file-meta">
                  Prefilled from {trend.prefill.sources.length} account
                  balance{trend.prefill.sources.length === 1 ? "" : "s"} you
                  have already entered. Change anything that is out of date.
                </p>
              )}
            </>}
          </article>
          </>}
        </>
      )}
    </section>
  );
}
export default InsightsView;
