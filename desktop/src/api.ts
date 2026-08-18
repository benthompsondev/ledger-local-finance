import { invoke } from "@tauri-apps/api/core";
import type { HomeDashboard } from "./types";

export async function loadHomeDashboard(periodDays: 30 | 90): Promise<HomeDashboard> {
  return invoke<HomeDashboard>("get_home_dashboard", { periodDays });
}
import type {
  AccountsPayload,
  ConfirmPayload,
  CreateAccountPayload,
  HomePacket,
  PlanPayload,
  PlanPreview,
  GoalsPayload,
  InsightsPayload,
  PlanningBalances,
  BackupPayload,
  DataSafetyPayload,
  LegacyRepairPreview,
  PreviewPayload,
  TransactionRow,
  TransactionsPayload,
  TransactionQuery,
  RepairResult,
  ResetResult,
  CategorySettingsPayload,
  SharedSettingsPayload,
  CsvMapping,
  AiSettings,
  AiAnswer,
  AiCoachingSummary,
  AiPayloadPreview,
  Insight,
  InsightExplanation,
  InsightFeed,
  NetWorthOverview,
  SpendingPatterns,
  UpcomingMoney,
} from "./types";

export async function loadHomeSummary(periodDays: 30 | 90): Promise<HomePacket> {
  return invoke<HomePacket>("get_home_summary", { periodDays });
}

export async function loadAccounts(): Promise<AccountsPayload> {
  return invoke<AccountsPayload>("list_accounts");
}

export async function createAccount(spec: {
  name: string;
  accountType: string;
  institution: string;
  openingBalance: number;
}): Promise<CreateAccountPayload> {
  return invoke<CreateAccountPayload>("create_account", { spec });
}

export async function setAccountSpendingAvailability(
  accountId: number,
  available: boolean,
): Promise<{ accounts: import("./types").Account[] }> {
  return invoke("set_account_spending_availability", {
    spec: { accountId, available },
  });
}

export async function pickImportFiles(): Promise<string[]> {
  return invoke<string[]>("pick_import_files");
}

export async function previewImport(
  paths: string[],
  files?: {
    path: string; accountId: number | null; mapping: CsvMapping | null;
    dateFormat?: string | null;
    bank?: string | null;
    importMode?: "new" | "reexport" | null;
  }[],
): Promise<PreviewPayload> {
  return invoke<PreviewPayload>("preview_import", { paths, files: files ?? null });
}

export async function applyCsvMapping(spec: {
  path: string;
  accountId: number;
  mapping: CsvMapping;
  saveProfile: boolean;
  profileName: string;
}): Promise<PreviewPayload> {
  return invoke<PreviewPayload>("apply_csv_mapping", { spec });
}

export async function confirmImport(
  files: {
    path: string; accountId: number; mapping?: CsvMapping | null;
    dateFormat?: string | null;
    bank?: string | null;
    importMode?: "new" | "reexport" | null;
  }[],
): Promise<ConfirmPayload> {
  return invoke<ConfirmPayload>("confirm_import", { files });
}
export const resetCsvProfile = (path: string) =>
  invoke<{ reset: boolean }>("reset_csv_profile", { path });

export async function loadTransactions(query: TransactionQuery): Promise<TransactionsPayload> {
  return invoke<TransactionsPayload>("list_transactions", { query });
}

export async function addManualTransaction(spec: {
  accountId: number;
  date: string;
  description: string;
  merchant: string;
  amount: number;
  direction: string;
  category: string;
  note: string;
  /** Keep a row identical to one already stored; the user confirmed it. */
  allowDuplicate?: boolean;
}): Promise<{ transaction: TransactionRow }> {
  return invoke<{ transaction: TransactionRow }>("add_manual_transaction", { spec });
}

export const loadPlan = () => invoke<PlanPayload>("get_plan_summary");
// incomeTarget and flexibleAllowance are deliberately absent: both are
// derived, and the engine computes them from the same helper it uses to
// display them, so a client copy could only ever disagree.
export const previewPlan = (spec: {
  mode: string; fixedObligations: number;
  savingsTarget: number; safetyBuffer: number; applyPreset: boolean;
  applyPreference?:boolean; savingsPreferenceStyle?:string; savingsPreferenceValue?:number;
}) => invoke<PlanPreview>("preview_plan", { spec });
export const savePlan = (spec: {
  month: string;
  mode: string;
  fixedObligations: number;
  savingsTarget: number;
  safetyBuffer: number;
  notes: string;
  fixedOverrideReason: string;
}) => invoke<PlanPayload>("save_plan", { spec });

export const loadGoals = () => invoke<GoalsPayload>("get_goals_summary");
export const createGoal = (spec: {
  name: string;
  goalType: string;
  targetAmount: number;
  currentAmount: number;
  targetDate: string;
  monthlyContribution: number;
  contributionFrequency:"monthly"|"quarterly";
  includeInPlan: boolean;
  showMilestones: boolean;
  notes: string;
  progressMethod: "manual" | "linked_account";
  linkedAccountRef: number | null;
}) => invoke<GoalsPayload>("create_goal", { spec });
export const updateGoal = (spec: {
  goalId:number; name:string; goalType:string; targetAmount:number;
  targetDate:string; monthlyContribution:number; includeInPlan:boolean;
  contributionFrequency:"monthly"|"quarterly";
  showMilestones:boolean;
  notes:string; progressMethod:"manual"|"linked_account";
  linkedAccountRef:number|null; status:"active"|"paused"|"completed"|"archived";
}) => invoke<GoalsPayload>("update_goal", { spec });
export const contributeGoal = (spec: {
  goalId: number;
  amount: number;
  date: string;
  note: string;
}) => invoke<GoalsPayload>("contribute_goal", { spec });

export const loadInsights = (periodDays: 30 | 90) =>
  invoke<InsightsPayload>("get_insights_summary", { periodDays });
// Planning balances live in Settings. They feed Safe to Spend and Plan and
// are never summed into a net-worth figure, so both calls return only the
// accounts and their latest snapshot.
export const loadPlanningBalances = () =>
  invoke<PlanningBalances>("get_planning_balances");

export const addAccountBalance = (spec: {
  accountRef: number;
  balance: number;
  asOfDate: string;
  note: string;
  periodDays: 30 | 90;
}) => invoke<PlanningBalances>("add_account_balance", { spec });
export const saveQuickNetWorth = (spec:{
  totalAssets:number; totalLiabilities:number; asOfDate:string; periodDays:30|90;
}) => invoke<InsightsPayload>("save_quick_net_worth", {spec});
export const loadAiSettings = () =>
  invoke<{ ai: AiSettings }>("get_ai_settings");
export const saveAiSettings = (spec: {
  enabled?: boolean; baseUrl?: string; model?: string;
  /** Omit to leave the stored key untouched. */
  apiKey?: string; scope?: string; months?: number; focus?: string;
  style?: string; essentialCategories?: string[];
}) => invoke<{ ai: AiSettings }>("save_ai_settings", { spec });
export const forgetAiKey = () => invoke<{ ai: AiSettings }>("forget_ai_key");
export const previewAiPayload = (scope?: string, months?: number) =>
  invoke<{ preview: AiPayloadPreview; scope: string }>(
    "ai_payload_preview", { scope, months },
  );
export const loadNetWorthTrend = () =>
  invoke<NetWorthOverview>("get_net_worth_trend");
export const saveNetWorthEntry = (entry: {
  month: string; cash: number; investments: number;
  otherAssets: number; liabilities: number; note?: string;
}) => invoke<NetWorthOverview>("save_net_worth_entry", {
  month: entry.month, cash: entry.cash, investments: entry.investments,
  otherAssets: entry.otherAssets, liabilities: entry.liabilities,
  note: entry.note ?? "",
});
export const deleteNetWorthEntry = (month: string) =>
  invoke<NetWorthOverview>("delete_net_worth_entry", { month });
export const pickExportFile = () => invoke<string | null>("pick_export_file");
export const exportTransactions = (path: string) =>
  invoke<{ exported: boolean; path: string; rows: number; columns: string[] }>(
    "export_transactions", { path },
  );
export const deleteTransaction = (id: number) =>
  invoke<{ deleted: boolean; id: number }>("delete_transaction", { id });
export const loadInsightFeed = () =>
  invoke<InsightFeed>("get_insight_feed");
export const loadUpcomingMoney = (asOfDate?: string) =>
  invoke<UpcomingMoney>("get_upcoming_money", { asOfDate });
export const loadSpendingPatterns = (months?: number) =>
  invoke<SpendingPatterns>("get_spending_patterns", { months });
export const explainInsight = (findingId: string) =>
  invoke<{ explanation: InsightExplanation; finding: Insight }>(
    "explain_insight", { findingId },
  );
export const askAi = (question: string) =>
  invoke<AiAnswer>("ai_ask", { question });
export const loadAiCoachingSummary = () =>
  invoke<AiCoachingSummary>("ai_coaching_summary");
export const testAiConnection = () =>
  invoke<{
    ok: boolean; message: string; provider: string; model: string;
  }>("test_ai_connection");

export const loadBackupStatus = () => invoke<BackupPayload>("get_backup_status");
export const createLocalBackup = () => invoke<BackupPayload>("create_backup");
export const pickBackupFile = () => invoke<string | null>("pick_backup_file");
export const restoreLocalBackup = (path: string) =>
  invoke<BackupPayload>("restore_backup", { path });
export const loadDataSafetyStatus = () =>
  invoke<DataSafetyPayload>("get_data_safety_status");
export const previewLegacyRepair = (paths: string[]) =>
  invoke<LegacyRepairPreview>("preview_legacy_repair", { paths });
export const runLegacyRepair = (paths: string[]) =>
  invoke<RepairResult>("repair_legacy_data", { paths });
export const resetFinancialData = (confirmation: string, completeReset: boolean) =>
  invoke<ResetResult>("reset_financial_data", { confirmation, completeReset });

export async function correctTransaction(spec: {
  id: number;
  category: string;
  note: string;
  applyToMatching: boolean;
  rememberRule: boolean;
  transactionType?:string;
  sharedExpenseOverride?:boolean;
  sharedUserSharePct?:number;
}): Promise<{
  transaction: TransactionRow;
  matching_updated: number;
  rule_saved: boolean;
  undo_available: boolean;
}> {
  return invoke("correct_transaction", {
    id: spec.id,
    category: spec.category,
    note: spec.note,
    applyToMatching: spec.applyToMatching,
    rememberRule: spec.rememberRule,
    transactionType: spec.transactionType,
    sharedExpenseOverride: spec.sharedExpenseOverride,
    sharedUserSharePct: spec.sharedUserSharePct,
  });
}

export const undoLastCategoryChange = () =>
  invoke<{restored:number;available:boolean}>("undo_last_category_change");
export const loadCategorySettings = () =>
  invoke<CategorySettingsPayload>("get_category_settings");
export const updateCategoryRule = (id:number, category:string, enabled:boolean) =>
  invoke<CategorySettingsPayload>("update_category_rule", {id, category, enabled});
export const deleteCategoryRule = (id:number) =>
  invoke<{deleted:boolean}>("delete_category_rule", {id});
export const setRecurringPreference = (
  merchantNormalized:string, status:string, displayName="", category="", applyCategory=false,
) => invoke<CategorySettingsPayload>("set_recurring_preference", {
  merchantNormalized, status, displayName, category, applyCategory,
});
export const setIncomeSourcePreference = (sourceNormalized:string,status:"confirmed"|"excluded") =>
  invoke<CategorySettingsPayload>("set_income_source_preference", {sourceNormalized,status});
export const loadReviewSummary = () => invoke<{count:number;high_priority:number;deferred:number;suggested:number;actionable:number}>("get_review_summary");
export const loadSharedSettings = () => invoke<SharedSettingsPayload>("get_shared_settings");
export const saveSharedSettings = (sharedWithName:string,defaultUserSharePct:number) =>
  invoke<SharedSettingsPayload>("save_shared_settings", {sharedWithName,defaultUserSharePct});
export const setSharedRule = (scopeType:string,scopeValue:string,userSharePct:number,enabled=true) =>
  invoke<SharedSettingsPayload>("set_shared_rule", {scopeType,scopeValue,userSharePct,enabled});
