export interface Freshness {
  state: string;
  headline: string;
  detail: string;
  latest_date?: string;
  days_since?: number | null;
  update_action?: string;
  coverage_warning?: string;
  account_activity_note?: string;
  cutoff_gap_days?: number;
  accounts?: {
    account_name: string;
    account_type: string;
    latest_date: string;
    earliest_date: string;
    tx_count: number;
    days_since: number;
    partial_current_period: boolean;
  }[];
}

export interface Verdict {
  state: string;
  headline: string;
  sentence: string;
  setup_steps?: { label: string; screen: string }[];
}

export interface SafeToSpend {
  available: boolean;
  reason: string;
  month: string;
  amount: number;
  weekly_amount: number;
  status: string;
  confidence: string;
  reserved: {
    basis?: string;
    balance_checked?: boolean;
    reliable_income?: number;
    income_months_used?: number;
    income_basis_label?: string;
    monthly_plan?: number;
    flexible_spent?: number;
    recurring_commitments?: number;
    liquid_balance?: number;
    available_balance?: number;
    excluded_balance?: number;
    stable_income_target?: number;
    income_expected?: number;
    stable_income_received?: number;
    stable_income_remaining?: number;
    future_income_forecast?: number;
    future_income_included?: boolean;
    spending_so_far?: number;
    flexible_allowance?: number;
    flexible_spending_so_far?: number;
    flexible_remaining?: number;
    cash_cushion_after_bills?: number;
    bills_remaining?: number;
    subscriptions_remaining?: number;
    savings_target?: number;
    savings_contributed_this_month?: number;
    savings_remaining?: number;
    nonmonthly_reserves?: number;
    debt_or_fee_reserve?: number;
    credit_card_liability?: number;
    buffer?: number;
    reconciliation?: BalanceReconciliation;
    plan_agreement?: PlanAgreement;
    balance_accounts?: {
      account_ref:number|null;account_name:string;account_kind:string;
      balance:number;available_for_spending:boolean;
    }[];
  };
  why: string[];
  setup_screen?: string;
}

export interface StableIncome {
  monthly_amount:number; basis:string; label:string; months_used:number; confirmed:boolean;
  sources?: {source:string;source_normalized:string;monthly_amount:number;months_used:number;status:string;kind?:string}[];
}

export interface BalanceReconciliation {
  ready:boolean;required_count:number;current_count:number;missing_count:number;
  out_of_date_count:number;imports_newer_than_balance:boolean;
  accounts:{account_ref:number|null;account_name:string;account_kind:string;as_of_date:string;last_activity:string;status:string;ready:boolean;imports_newer_than_balance:boolean}[];
}

export interface PlanAgreement {
  agreed:boolean;needs_review:boolean;detected:number;saved:number;difference:number;
  override_active:boolean;override_reason:string;detected_at_save:number|null;reason:string;
}

export interface Progress {
  month: string;
  mtd_income: number;
  mtd_spending: number;
  projected_net: number;
  projected_spending: number;
  savings_target: number;
  savings_gap: number;
  risk_level: string;
  payroll_received?: number;
  shared_reimbursements?: number;
  net_cash_flow?: number;
  amount_kept?: number;
  shortfall?: number;
  non_payroll_inflow?: number;
  show_payroll_breakout?: boolean;
}

export interface MonthlyReview {
  available: boolean;
  month: string;
  net: number;
  net_delta: number;
  spending_delta: number;
  savings_rate: number;
}

export interface HomePacket {
  generated_for: string;
  analysis: AnalysisContext;
  freshness: Freshness;
  verdict: Verdict;
  safe_to_spend: SafeToSpend;
  progress: Progress;
  monthly_review: MonthlyReview;
  goal: GoalSummary | null;
  changes: Finding[];
  actions: ActionItem[];
  period_days: 30 | 90;
  period_label: string;
  period_start: string;
  period_end: string;
  stable_income: StableIncome;
  period_result: {
    label:string;amount:number;positive:boolean;sentence:string;
    comparison_available:boolean;comparison_delta:number;comparison_delta_abs:number;
    comparison_basis:"rolling"|"month_to_date";
    comparison_direction:"improved"|"lower";comparison_label:string;
    prior_start:string;prior_end:string;
  };
}

export interface Finding {
  id: string;
  title: string;
  why_it_matters: string;
  delta: number;
  direction: string;
  current?: number | null;
  previous?: number | null;
  period?: string;
  drill?: {
    page?: string;
    category?: string;
    merchant?: string;
    cashflow_role?: "income" | "spending" | "net";
    start_date?: string;
    end_date?: string;
  };
}

export interface ActionItem {
  id: string;
  title: string;
  why: string;
  label: string;
  screen: string;
  category?: string;
}

export interface Account {
  id: number;
  name: string;
  type: string;
  type_label: string;
  institution: string;
  currency: string;
  tx_count: number;
  last_activity: string;
  available_for_spending: boolean;
}

/** One account's latest recorded balance. A snapshot the user typed, never
 *  inferred from transactions. */
export interface AccountBalance {
  account_ref?: number | null;
  account_name: string;
  account_kind: string;
  balance: number;
  as_of_date: string;
  currency?: string;
  notes?: string;
}

/** What the Settings "Planning balances" section reads.
 *
 *  Deliberately carries no assets, liabilities or net-worth total. These are
 *  planning inputs for Safe to Spend and Plan; the monthly readings on
 *  Insights are the only net worth SignalSpace reports.
 */
export interface PlanningBalances {
  accounts: Account[];
  balances: AccountBalance[];
}

export interface AccountType {
  value: string;
  label: string;
}

export interface AccountsPayload {
  accounts: Account[];
  account_types: AccountType[];
}

export interface CreateAccountPayload extends AccountsPayload {
  created_id: number;
}

export interface PreviewSampleRow {
  date: string;
  description: string;
  category: string;
  amount: number;
  direction: string;
}

export interface CsvMapping {
  date_col: string;
  desc_col: string;
  amount_mode: "" | "signed" | "split";
  amount_col: string;
  debit_col: string;
  credit_col: string;
  positive_is: "credit";
}

export interface CsvMappingInfo {
  headers: string[];
  rows: string[][];
  delimiter: string;
  encoding: string;
  suggested: CsvMapping;
  error: string;
}

/** What the importer decided about one file, shown before anything is saved. */
export interface ImportReceipt {
  rows_parsed: number;
  rows_skipped: number;
  zero_value_rows: number;
  first_date: string;
  last_date: string;
  months_spanned: number;
  money_in: number;
  money_out: number;
  date_format: string;
  decimal_separator: string;
  delimiter: string;
  encoding: string;
  /** Which bank layout was recognised, if any. */
  bank: string;
  has_header: boolean;
  skipped_lines: number;
  notes: string[];
  problems: string[];
}

export interface PreviewFile {
  path: string;
  filename: string;
  detected: string;
  label: string;
  confidence: string;
  already_imported: boolean;
  suggested_account_type: string;
  suggested_account_ref: number | null;
  csv_profile_account_type: string;
  csv_profile_id: number | null;
  needs_mapping: boolean;
  blocked: boolean;
  receipt: ImportReceipt | null;
  /** Offered when a date column could be read two ways and nothing settles it. */
  date_format_choices: { value: string; label: string; example: string }[];
  /** Offered when a file needs help; naming the bank beats mapping columns. */
  bank_choices: { value: string; label: string; country: string; note: string }[];
  mapping: CsvMapping | null;
  mapping_info: CsvMappingInfo | null;
  dedup_pending: boolean;
  /** A partial overlap cannot be resolved safely from transaction rows. */
  dedup_choice_required: boolean;
  provenance_status: "pending" | "blocked" | "new" | "reexport" | "ambiguous";
  provenance_note: string;
  tx_count: number;
  new_transaction_count: number;
  duplicate_count: number;
  statement_period: string;
  date_start: string;
  date_end: string;
  income: number;
  spending: number;
  sample: PreviewSampleRow[];
  errors: string[];
  error: string;
}

export interface PreviewPayload extends AccountsPayload {
  files: PreviewFile[];
}

export interface ImportFileResult {
  filename: string;
  account_name: string;
  batch_id: number;
  inserted: number;
  skipped: number;
  flagged: number;
  provenance: "new" | "reexport" | "ambiguous";
  import_mode: "new" | "reexport";
}

export interface ImportTotals {
  inserted: number;
  skipped: number;
  flagged: number;
}

export interface ConfirmPayload {
  results: ImportFileResult[];
  totals: ImportTotals;
}

/** Frontend-accumulated result of a sequential multi-file import: engine
 * results for the files that saved, plus per-file failures that saved
 * nothing. */
export interface ImportOutcome extends ConfirmPayload {
  failures: { filename: string; message: string }[];
}

export interface TransactionRow {
  id: number;
  date: string;
  account_name: string;
  merchant: string;
  description: string;
  category: string;
  category_source: string;
  amount: number;
  posted_amount: number;
  direction: string;
  flow_label: string;
  currency: string;
  notes: string;
  is_flagged: boolean;
  flag_reason: string;
  transaction_type: string;
  suggested_category: string;
  suggestion_reason: string;
  suggested_transaction_type: string;
  type_suggestion_reason: string;
  household_cost: number;
  user_share: number;
  other_share: number;
  user_share_pct: number;
  is_shared: boolean;
  shared_source: string;
  shared_expense_override: number | null;
  raw_description: string;
  review_priority: number;
  review_reason: string;
}

export interface TransactionsPayload {
  transactions: TransactionRow[];
  categories: string[];
  accounts: Account[];
  total: number;
  filtered_count: number;
  filtered_total: number;
  filtered_total_label: string;
  offset: number;
  limit: number;
  review: { high_priority: number; deferred: number; suggested: number; actionable:number; quick_review:number };
}

export interface TransactionQuery {
  accountRef: number | null;
  search: string;
  category: string;
  direction: string;
  startDate: string;
  endDate: string;
  sort: string;
  descending: boolean;
  offset: number;
  limit: number;
  flaggedOnly?: boolean;
  suggestedOnly?: boolean;
  quickReview?: boolean;
  cashflowRole?: "" | "income" | "spending" | "net";
}

export interface PlanRecord {
  month: string;
  mode: string;
  income_target: number;
  spending_target: number;
  savings_target: number;
  fixed_obligations?: number;
  flexible_allowance?: number;
  safety_buffer?: number;
  fixed_override_reason?: string;
  detected_commitments_at_save?: number | null;
  notes?: string;
  /** Persisted save stamps, so "Saved" survives a reload and a restart. */
  created_at?: string;
  updated_at?: string;
}

/** Optional AI assistance. Off unless the user turns it on. */
/** One month's reading. Every figure was entered by hand, so nothing here
 *  is projected and nothing is derived from statements. */
export interface NetWorthEntry {
  month: string;
  cash: number;
  investments: number;
  other_assets: number;
  liabilities: number;
  /** cash + investments + other assets − liabilities. */
  net: number;
  note: string;
  /** Movement on the previous recorded month; null for the first one. */
  change: number | null;
  /** Absent when the previous month was zero or negative, where a
   *  percentage would be meaningless rather than merely uninteresting. */
  change_pct: number | null;
}

export interface NetWorthMove {
  from_month: string;
  to_month: string;
  amount: number;
  from_net: number;
  to_net: number;
  /** Calendar months between the two readings, not rows between them. */
  months_apart: number;
}

/** A suggested reading built from account balances already entered. Always a
 *  suggestion: it only knows the accounts SignalSpace has balances for. */
export interface NetWorthPrefill {
  month: string;
  cash: number;
  investments: number;
  other_assets: number;
  liabilities: number;
  net: number;
  has_balances: boolean;
  sources: {
    account: string; kind: string; field: string;
    amount: number; as_of_date: string; currency: string;
  }[];
  /** Balances left out of the arithmetic because they are not in the local
   *  currency. SignalSpace invents no exchange rate, so these are reported
   *  rather than converted or added. */
  excluded: {
    account: string; kind: string; field: string;
    amount: number; as_of_date: string; currency: string;
  }[];
  excluded_currencies: string[];
  local_currency: string;
}

export interface NetWorthOverview {
  has_entries: boolean;
  reason: string;
  entries: NetWorthEntry[];
  prefill: NetWorthPrefill;
  months_recorded: number;
  current?: NetWorthEntry;
  /** Each of these is that exact calendar period, or null. Null means the
   *  comparison month was never recorded; it must never render as +$0,
   *  which would claim the reader did not move. */
  month_over_month?: NetWorthMove | null;
  three_month?: NetWorthMove | null;
  twelve_month?: NetWorthMove | null;
  /** Whatever two readings exist, adjacent or not. Always label it with
   *  both months, because it may span any amount of time. */
  since_last_reading?: NetWorthMove | null;
  since_first?: (NetWorthMove & { reading_count: number });
  average_move?: number | null;
  best_month?: NetWorthEntry | null;
  worst_month?: NetWorthEntry | null;
  component_moves?: {
    field: string; label: string;
    now: number; before: number; change: number;
  }[] | null;
  /** Which two readings the component table compares. They are not
   *  necessarily one month apart. */
  component_from_month?: string;
  component_to_month?: string;
  component_months_apart?: number | null;
  /** Measured movement against what the statements say was kept, for months
   *  where both ends are real. The gap is usually investment growth. */
  explained?: {
    month: string; measured: number; kept: number; unexplained: number;
    /** Always 1. A one-month kept figure cannot explain a longer change. */
    months_apart: number;
  }[];
  material_move?: number;
}

/** One thing SignalSpace noticed, with the arithmetic already done. */
export interface Insight {
  id: string;
  kind: string;
  title: string;
  /** A complete sentence with real figures. Renders with no AI at all. */
  claim: string;
  tone: "watch" | "good";
  confidence: "high" | "medium" | "low";
  /** Which engine function produced this, so two copies cannot drift. */
  basis: string;
  chart: string;
  /** Impact in dollars a month, normalised by the engine so a yearly and a
   *  two-month figure can be ordered against each other. */
  monthly_impact?: number;
  /** monthly_impact times a documented evidence-and-actionability weight. */
  rank: number;
  /** The one number worth reading before the sentence. Never pre-formatted:
   *  the engine sends the amount, this app decides how currency looks. */
  figure?: number | null;
  figure_kind?: "money" | "count" | "";
  figure_caption?: string;
  /** The rows the claim was computed from. */
  evidence?: InsightEvidence[];
  /** Sample size, timeframe, and what the comparison left out. */
  evidence_note?: string;
  drill?: {
    page?: string;
    category?: string;
    merchant?: string;
    start_date?: string;
    end_date?: string;
  } | null;
  transaction_ids?: number[];
  superseded_by?: string;
  /** What this finding is about. Two cards sharing a subject are the same
   *  news, and only the better-evidenced one is shown. */
  subject?: string;
  category?: string;
  merchant?: string;
  days?: DayOfWeekRow[];
  weekend_average?: number;
  weekday_average?: number;
  premium?: number;
  annual_impact?: number;
  detail?: Record<string, unknown>;
}

/** One line of the working behind a finding. */
export interface InsightEvidence {
  label: string;
  value: number | null;
  kind: "money" | "count" | "text";
  note: string;
  /** A change rather than a level, so it is shown with a direction. */
  signed?: boolean;
}

export interface InsightFeed {
  analysis: AnalysisContext;
  concerns: Insight[];
  /** True findings that lost the ranking. Offered behind a click, never in
   *  the main list, so the page stays a few things rather than a report. */
  also_noticed?: Insight[];
  positive: Insight | null;
  missing_data: string | null;
  complete_months: number;
  share_view: string;
}

export interface InsightExplanation {
  text: string;
  unsupported_removed: string[];
  provider: string;
  model: string;
  bytes_sent: number;
  attempts: number;
}

export interface DayOfWeekRow {
  weekday: number;
  name: string;
  total: number;
  average: number;
  transactions: number;
  occurrences: number;
}

export interface CalendarDay {
  date: string;
  amount: number;
  count: number;
  weekday: number;
  month: string;
}

export interface SpendingPatterns {
  calendar: {
    available: boolean;
    reason: string;
    start?: string;
    end?: string;
    days: CalendarDay[];
    total?: number;
    spending_days?: number;
    quiet_days?: number;
    busiest_day?: CalendarDay | null;
    typical_spending_day?: number;
  };
  day_of_week: {
    available: boolean;
    reason: string;
    days: DayOfWeekRow[];
    overall_average?: number;
    dearest?: DayOfWeekRow;
    cheapest?: DayOfWeekRow;
    meaningful?: boolean;
    weekend_average?: number;
    weekday_average?: number;
    weekend_premium?: number;
    lookback_days?: number;
  };
  year_ago: {
    available: boolean;
    reason: string;
    month?: string;
    year_ago_month?: string;
    through_day?: number;
    current_total?: number;
    year_ago_total?: number;
    delta?: number;
    percent?: number;
    categories?: { category: string; current: number; year_ago: number; delta: number }[];
    months_of_history?: number;
  };
}

export interface AiProvider {
  value: string;
  label: string;
  base_url: string;
  model: string;
  note: string;
}

export interface AiSettings {
  enabled: boolean;
  base_url: string;
  model: string;
  /** Populated for the read that repaired SignalSpace's retired MiniMax default. */
  model_migrated_from: string;
  scope: string;
  months: number;
  focus: string;
  style: string;
  essential_categories: string[];
  api_key_set: boolean;
  /** How the key is held at rest. Cloud keys use Windows user protection. */
  key_storage: "none" | "windows_protected" | "unavailable";
  /** Present only when an existing protected key cannot be unlocked. */
  key_error: string;
  /** The address points at this computer, so nothing leaves it. */
  is_local: boolean;
  /** False for a local model, which has nobody to authenticate to. */
  key_required: boolean;
  providers: AiProvider[];
  scopes: { value: string; label: string; note: string }[];
  focus_options: { value: string; label: string }[];
  style_options: { value: string; label: string }[];
  essential_category_options: string[];
}

/** Exactly what would leave the machine, built from the payload itself. */
export interface AiPayloadPreview {
  fields: string[];
  transaction_count: number;
  merchant_count: number;
  bytes: number;
  json: string;
}

export interface AiAnswer {
  answer: string;
  scope: string;
  sent: AiPayloadPreview;
  provider: string;
  model: string;
  data_coverage: {
      latest_transaction_date?: string;
      days_since_latest_activity?: number | null;
    latest_data_month?: string;
    latest_complete_month?: string;
    latest_month_complete?: boolean;
    latest_month_caveat?: string;
  };
  grounding_status:
    | "figures_matched"
    | "figures_matched_after_retry"
    | "figures_removed";
  grounding_note: string;
  provider_attempts: number;
  finish_reason: string;
}

export interface AiCoachingSummary {
  profile: {
    focus: string;
    response_style: string;
    categories_to_treat_as_essential: string[];
  };
  snapshot: {
    comparison_available: boolean;
    period: {
      month: string;
      compared_with: string;
      uses_complete_months: boolean;
      ignored_partial_months: string[];
    };
    latest_result: {
      income: number | null;
      spending: number | null;
      kept: number | null;
      savings_rate: number | null;
    };
    change_from_prior: {
      income: number | null;
      spending: number | null;
      kept: number | null;
    };
    category_increases: AiCategoryChange[];
    category_decreases: AiCategoryChange[];
    history_months: number;
    action_guardrails: {
      safe_to_spend_available: boolean;
      safe_to_spend: number | null;
      safe_to_spend_reason: string;
      historical_kept_is_not_cash_available_now: boolean;
      exact_transfer_amount_allowed: boolean;
    };
  };
  months: MonthlyAggregate[];
  data_coverage: AiAnswer["data_coverage"];
}

export interface AiCategoryChange {
  category: string;
  current: number;
  previous: number;
  change: number;
}

/** The one income baseline every screen renders. Never recompute it. */
export interface IncomeBasis {
  amount: number;
  months_used: number;
  months: { month: string; income: number }[];
  first_month: string;
  last_month: string;
  confidence: string;
  basis_label: string;
}

/**
 * Which dates the figures on a screen actually describe. Shared by every
 * view so none of them can quietly disagree, and so a stale import is
 * never labelled "this month".
 */
export interface AnalysisContext {
  has_data: boolean;
  data_through: string;
  data_from: string;
  raw_latest: string;
  ignored_dates: { date: string; transactions: number }[];
  ignored_count: number;
  analysis_month: string;
  analysis_month_label: string;
  data_month: string;
  data_month_label: string;
  month_source: "data" | "calendar";
  days_behind: number;
  confidence: "none" | "low" | "high";
  state: "empty" | "unsupported" | "current" | "recent" | "stale";
  label: string;
}

/**
 * How the last finished month compared with what was intended for it.
 * Every figure is computed by the engine; the screen only formats them.
 */
export interface LastPlanResult {
  available: boolean;
  reason: string;
  month?: string;
  month_label?: string;
  intended_kept?: number;
  actual_kept?: number;
  actual_kept_abs?: number;
  difference?: number;
  difference_abs?: number;
  met?: boolean;
  money_in?: number;
  spending?: number;
  target_is_zero?: boolean;
  kept_is_negative?: boolean;
  coverage_note?: string;
  planned_on?: string;
}

/** At most one thing about this month worth interrupting for. */
export interface PlanNotice {
  level: "info" | "warn";
  headline: string;
  detail: string;
  action: string;
  screen: string;
}

export interface PlanPayload {
  month: string;
  analysis?: AnalysisContext;
  last_plan_result?: LastPlanResult;
  plan_notice?: PlanNotice | null;
  saved: PlanRecord | null;
  proposal: PlanRecord;
  working_plan: PlanRecord;
  fixed_obligations_suggestion: number;
  income_basis: IncomeBasis;
  stable_income: StableIncome;
  reliable_income: StableIncome;
  payroll_income: StableIncome;
  income_confirmation_candidates: {
    source_normalized:string;source:string;monthly_amount:number;
    months_seen:number;deposits_seen:number;cadence:string;reason:string;
  }[];
  fixed_commitments: {merchant:string;category:string;amount:number;household_amount:number;other_share:number;user_share_pct:number;is_shared:boolean;kind:string;basis:string;cadence:string;monthly_setaside:number}[];
  /** Bills that do not arrive monthly, and the monthly share reserved for them. */
  nonmonthly_commitments: {
    merchant:string;category:string;cadence:string;amount:number;
    monthly_setaside:number;expected_next:string;confidence:string;note:string;
  }[];
  nonmonthly_monthly_reserve: number;
  nonmonthly_annual_total: number;
  price_changes: {
    merchant:string;previous_amount:number;current_amount:number;
    delta:number;changed_on:string;annual_impact:number;
  }[];
  equation: PlanEquation;
  outlook: {month:string;label:string;stable_income:number;fixed:number;nonmonthly_reserves:number;savings:number;buffer:number;flexible:number;known_recurring:{merchant:string;amount:number;household_amount:number;is_shared:boolean}[];days:number}[];
  preview?: PlanPreview;
  safe_to_spend: SafeToSpend;
  outcome: Verdict;
  risk_level: string;
  forecast: Progress & {
    projected_income: number; confidence: string; risk_level: string;
    expected_income_forecast:number;expected_income_remaining:number;
    expected_projected_net:number;
    // How the expectation was reached, and what was due but never arrived.
    // The engine decides all of it; the screen only renders these.
    expected_income_basis:string;missed_income_total:number;
    income_note:string;
    // Commitments still to land, split so an occurrence whose date has
    // already passed is never displayed as an ordinary upcoming bill.
    overdue_bills_total:number;overdue_bills_count:number;
    overdue_bills_note:string;committed_bills_total:number;
    upcoming_bills:{
      merchant:string;amount:number;cadence:string;
      expected_next:string|null;status:string;
    }[];
  };
  readiness: {
    forecast_ready: boolean;
    plan_confirmed: boolean;
    balance_count: number;
    has_spendable_balance: boolean;
    reconciliation: BalanceReconciliation;
    plan_agreement: PlanAgreement;
    coverage_complete: boolean;
    cash_checked: boolean;
    history_sufficient: boolean;
    history_days:number;
    uncertain_commitments:number;
    missing: {id:string;label:string;detail:string;action:string;screen:string}[];
    advisories: {id:string;label:string;detail:string;action:string;screen:string}[];
    review: {
      income_reviewed: boolean;
      commitments_reviewed: boolean;
      complete: boolean;
      last_reviewed: string;
    };
  };
  modes: { value: string; label: string; description: string }[];
  warning?: string;
}

export interface PlanEquation {
  income:number; fixed:number; nonmonthly_reserves:number; savings:number; buffer:number; flexible:number;
  unallocated:number; coherent:boolean; explanation:string;
}
export interface PlanPreview {equation:PlanEquation;values:{savings_target:number;safety_buffer:number;flexible_allowance:number;nonmonthly_reserves:number}}

export interface GoalSummary {
  id: number;
  name: string;
  type: string;
  type_label: string;
  target_amount: number;
  current_amount: number;
  progress_pct: number;
  gap: number;
  target_date?: string;
  planned_monthly_contribution: number;
  contribution_frequency:"monthly"|"quarterly";
  contribution_target:number;
  contributed_this_period:number;
  contribution_period_label:string;
  contribution_state:string;
  pace_state: string;
  progress_method: "manual" | "linked_account" | "legacy_metric";
  progress_method_label: string;
  source_label: string;
  status:"active"|"paused"|"completed"|"archived";
  required_monthly_pace:number|null;
  next_contribution:number;
  months_remaining:number|null;
  milestone:string;
  show_milestones:boolean;
  contribution_streak_months:number;
  linked_account?: Account | null;
  include_in_plan?:number;
  notes?:string;
}

export interface GoalsPayload {
  goals: GoalSummary[];
  goal_types: { value: string; label: string }[];
  templates: {type:string;label:string;name:string;primary:boolean}[];
  accounts: Account[];
  created_id?: number;
}

export interface MonthlyAggregate {
  month: string;
  /** Decided by the engine from statement coverage, never in the view. */
  complete: boolean;
  income: number;
  spending: number;
  net: number;
  savings_rate: number;
}

export interface InsightsPayload {
  month: string;
  analysis: AnalysisContext;
  period_days: 30 | 90;
  period_label: string;
  period_start: string;
  period_end: string;
  share_view:"personal"|"household";
  monthly: MonthlyAggregate[];
  categories: { category: string; total: number; pct: number }[];
  income_sources: IncomeSourceItem[];
  income_total: number;
  stable_income: StableIncome;
  cash_flow_summary:{money_in:number;shared_reimbursements:number;stable_income_received:number;spending:number;fixed_recurring:number;fixed_monthly_estimate:number;net_cash_flow:number;amount_kept:number;prior_amount_kept:number;kept_change:number;kept_change_abs:number;kept_direction:"up"|"down";prior_available:boolean;comparison_label:string};
  merchants: { merchant: string; category: string; total: number; visits: number }[];
  net_worth: {
    net_worth: number;
    total_assets: number;
    total_liabilities: number;
    as_of_date: string;
    missing: string[];
    configured: boolean;
    calculated: boolean;
    status: "not_configured" | "partial" | "configured" | "mixed_currency";
    missing_account_count: number;
  };
  quick_net_worth:{total_assets:number;total_liabilities:number;net_worth:number;as_of_date:string}|null;
  balances: { account_name: string; account_kind: string; balance: number; as_of_date: string }[];
  accounts: Account[];
  findings: Finding[];
  pace: SpendingPace;
  flexible_pace: SpendingPace;
  category_pace: CategoryPace;
  category_movers: {
    increase: CategoryPaceItem | null;
    decrease: CategoryPaceItem | null;
  };
  recurring: {
    merchant: string;
    category: string;
    avg_amount: number;
    months_seen: number;
    total: number;
    tx_count: number;
    confidence: number;
    cadence: string;
    last_seen: string;
    expected_next: string | null;
    recurring_status: string;
    merchant_normalized: string;
    household_amount: number;
    other_share: number;
    is_shared: boolean;
    user_share_pct: number;
  }[];
}

export interface CashflowMonth {
  month: string;
  income: number;
  spending: number;
  net: number;
  savings_rate: number;
  complete: boolean;
}

export interface PacePoint {
  day: number;
  cumulative: number;
}

export interface SpendingPace {
  available: boolean;
  month: string;
  previous_month: string;
  /** Last day of the current month covered. Same as current_through_day. */
  day: number;
  /** Current month always runs through the analysis date. */
  current_through_day: number;
  /** Capped at the prior month's final day; lower than current when months differ. */
  comparison_through_day: number;
  days_in_month: number;
  previous_days_in_month: number;
  current: PacePoint[];
  previous: PacePoint[];
  /** True month-to-date spending. Reconciles with the spending headline. */
  current_total: number;
  /** Current spending through comparison_through_day. Use for equal-span deltas. */
  current_total_comparable: number;
  previous_total_same_day: number | null;
  delta: number | null;
  current_complete: boolean;
  average_90_day: PacePoint[];
  average_90_day_total: number | null;
  average_90_day_delta: number | null;
  average_90_day_month_count: number;
  average_90_day_reason: string;
}

export interface CategoryPaceItem {
  category: string;
  amount: number;
  share: number;
  /** Null when the previous month was not imported far enough to compare
   *  against. Zero means the previous month really did spend nothing here. */
  previous: number | null;
  /** Null whenever `previous` is. Never render higher/lower wording from it. */
  delta: number | null;
  average?: number | null;
  average_delta?: number | null;
}

export interface CategoryPace {
  available: boolean;
  month: string;
  previous_month: string;
  /** Last day of the current month covered. Same as current_through_day. */
  through_day: number;
  current_through_day: number;
  /** Capped at the prior month's final day; lower than current when months differ. */
  comparison_through_day: number;
  previous_days_in_month: number;
  /** Whether the previous month was imported far enough to compare against.
   *  False means every item's `previous` and `delta` are null, and only the
   *  amount and share can honestly be shown. */
  comparison_available: boolean;
  /** True month-to-date total. Reconciles with the spending headline. */
  total: number;
  /** Total through comparison_through_day, the basis for per-item deltas. */
  comparable_total: number;
  has_average?: boolean;
  average_month_count?: number;
  items: CategoryPaceItem[];
}

export interface IncomeSourceItem {
  source: string;
  source_normalized: string;
  category: string;
  total: number;
  tx_count: number;
  avg_amount: number;
  pct: number;
  stable_status: string;
}

export interface HomeDashboard {
  generated_for: string;
  analysis: AnalysisContext;
  available: boolean;
  cashflow: CashflowMonth[];
  period_days: 30 | 90;
  period_label: string;
  period_start: string;
  period_end: string;
  pace: SpendingPace;
  flexible_pace: SpendingPace;
  categories: CategoryPace;
  category_pace: CategoryPace;
  income_sources: IncomeSourceItem[];
  income_total: number;
  stable_income: StableIncome;
  merchants: { merchant: string; category: string; total: number; visits: number }[];
}

export interface CategorySettingsPayload {
  categories: string[];
  rules: {id:number;merchant_normalized:string;category:string;enabled:number;hit_count:number}[];
  recurring: InsightsPayload["recurring"];
  recurring_preferences: {id:number;merchant_normalized:string;status:string;display_name?:string;category?:string}[];
  income_sources:{source_normalized:string;source:string;tx_count:number;months_seen:number;total:number;status:string}[];
}

export interface SharedSettingsPayload {
  settings:{shared_with_name:string;default_user_share_pct:number;partner_matcher:string};
  rules:{id:number;scope_type:"merchant"|"recurring"|"category";scope_value:string;user_share_pct:number;enabled:number}[];
  categories:string[];
  merchants:{merchant_normalized:string;merchant:string;category:string;tx_count:number;total:number}[];
  recurring:{merchant:string;merchant_normalized:string;category:string;avg_amount:number}[];
}

/** Filters a drill-down applies when landing on the Transactions screen. */
/** One thing SignalSpace expects to happen, drawn from a rhythm it has seen.
 *  An expected date is an observation about the past stated forward, never
 *  a claim that the charge will occur. */
export interface UpcomingItem {
  kind: "income" | "bill";
  key: string;
  label: string;
  amount: number;
  expected_date: string;
  days_away: number;
  cadence: string;
  /** "usually once a month" — the rhythm in words, not a field name. */
  cadence_note: string;
  confidence: "low" | "medium" | "high";
  last_seen: string;
  /** The last few real charges behind the estimate. */
  recent: { date: string; amount: number }[];
  drill?: {
    merchant?: string; search?: string; category?: string;
    start_date?: string; end_date?: string;
  };
}

export interface UpcomingMoney {
  available: boolean;
  reason: string;
  window_start: string;
  window_end: string;
  horizon_days?: number;
  /** The last day the imported statements reach. Deliberately separate
   *  from window_start: the window is the real calendar, this is evidence. */
  data_through: string;
  data_age_days: number;
  staleness: "current" | "getting_stale" | "stale" | "no_data";
  staleness_note: string;
  items: UpcomingItem[];
  income: UpcomingItem[];
  bills: UpcomingItem[];
  expected_in_total: number;
  expected_out_total: number;
  income_count: number;
  bill_count: number;
  next_income: UpcomingItem | null;
  out_before_next_income: number | null;
  summary: string;
}

/** One set of finances. Profiles isolate by directory: each has its own
 *  database, so a profile that is not active is not open at all. */
export interface Profile {
  id: string;
  name: string;
  created_at: string;
  active: boolean;
  /** The first profile. Its directory is the data root, which is how an
   *  existing installation became a profile without moving anything, and
   *  why it cannot be deleted. */
  is_default: boolean;
  has_data: boolean;
  path: string;
}

export interface ProfileList {
  active: string;
  profiles: Profile[];
}

export interface TxPrefill {
  category?: string;
  search?: string;
  startDate?: string;
  endDate?: string;
  quickReview?: boolean;
  flaggedOnly?: boolean;
  suggestedOnly?: boolean;
  cashflowRole?: "" | "income" | "spending" | "net";
  categoryComparison?: {
    category: string;
    current: number;
    previous: number;
    delta: number;
    throughDay: number;
    previousMonth: string;
  };
}

export interface BackupItem {
  path: string;
  name: string;
  created_at: string;
  size_bytes: number;
}

export interface BackupPayload {
  data_directory: string;
  database_name: string;
  backups: BackupItem[];
  created?: string;
  restored?: string;
  pre_restore_backup?: string;
}

export interface LegacyRepairBatch {
  batch_id: number;
  filename: string;
  imported_at: string;
  row_count: number;
  affected_rows: number;
  manual_rows: number;
  account_name: string;
  account_type: string;
  source_selected?: boolean;
  matched_rows?: number;
  unmatched_rows?: number;
  ready?: boolean;
}

export interface LegacyRepairPreview {
  needed: boolean;
  ready?: boolean;
  batch_count: number;
  affected_rows: number;
  manual_rows: number;
  review_before: number;
  matched_rows?: number;
  unmatched_rows?: number;
  batches: LegacyRepairBatch[];
}

export interface ResetPreview {
  transaction_count: number;
  import_count: number;
  account_count: number;
  plan_count: number;
  goal_count: number;
  balance_count: number;
  /** Monthly net worth readings. Typed by hand, so unlike transactions they
   *  cannot be recovered by importing a statement again. */
  net_worth_reading_count: number;
  rule_count: number;
}

export interface DataSafetyPayload {
  repair: LegacyRepairPreview;
  reset: ResetPreview;
}

export interface RepairResult {
  repaired: boolean;
  backup_path: string;
  backup_name: string;
  batches_repaired: number;
  rows_repaired: number;
  manual_rows_preserved: number;
  signs_changed: number;
  categories_changed: number;
  review_before: number;
  review_after: number;
  message: string;
}

export interface ResetResult {
  reset: boolean;
  complete_reset: boolean;
  backup_path: string;
  backup_name: string;
  removed: ResetPreview;
  message: string;
}
