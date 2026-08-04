// Ledger native desktop shell. Every frontend capability is one narrow,
// typed Tauri command that forwards a single JSON request to the packaged
// Python engine sidecar over stdin/stdout. No HTTP server, no localhost
// listener, no external browser. Release builds hide the console window.
#![cfg_attr(all(windows, not(debug_assertions)), windows_subsystem = "windows")]

use std::io::Write;
use std::path::PathBuf;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tauri::path::BaseDirectory;
use tauri::{AppHandle, Manager};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_shell::process::CommandEvent;
use tauri_plugin_shell::ShellExt;

/// Ordinary engine requests read the local SQLite database.
const ENGINE_TIMEOUT_SECS: u64 = 45;
/// Statement parsing (PDF extraction across several files) needs longer.
const IMPORT_TIMEOUT_SECS: u64 = 180;

fn ledger_data_dir() -> Result<PathBuf, String> {
    if let Some(override_path) = std::env::var_os("LEDGER_DATA_DIR") {
        if !override_path.is_empty() {
            return Ok(PathBuf::from(override_path));
        }
    }
    let local = std::env::var_os("LOCALAPPDATA")
        .ok_or_else(|| "Windows LOCALAPPDATA is unavailable.".to_string())?;
    Ok(PathBuf::from(local).join("Ledger"))
}

/// Best-effort local error log so the shell never fails silently, even when
/// the sidecar itself could not start and therefore could not log anything.
fn log_shell_error(context: &str, detail: &str) {
    let Ok(data_dir) = ledger_data_dir() else {
        return;
    };
    let logs = data_dir.join("logs");
    if std::fs::create_dir_all(&logs).is_err() {
        return;
    }
    if let Ok(mut file) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(logs.join("native-shell.log"))
    {
        let _ = writeln!(
            file,
            "{} ERROR {}: {}",
            chrono::Local::now().format("%Y-%m-%d %H:%M:%S"),
            context,
            detail
        );
    }
}

fn decode_engine_response(stdout: &[u8]) -> Result<Value, String> {
    let response: Value = serde_json::from_slice(stdout)
        .map_err(|error| format!("Ledger engine returned invalid JSON: {error}"))?;
    if response.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err(response
            .get("error")
            .and_then(Value::as_str)
            .unwrap_or("Ledger engine failed without an error message.")
            .to_string());
    }
    response
        .get("data")
        .cloned()
        .ok_or_else(|| "Ledger engine returned no data.".to_string())
}

/// Run one one-shot engine request: spawn the sidecar, write a single JSON
/// line, collect a single JSON line back, and let the process exit.
async fn run_engine(
    app: &AppHandle,
    action: &str,
    params: Value,
    timeout_secs: u64,
) -> Result<Value, String> {
    let result = run_engine_inner(app, action, params, timeout_secs).await;
    if let Err(error) = &result {
        log_shell_error(action, error);
    }
    result
}

async fn run_engine_inner(
    app: &AppHandle,
    action: &str,
    params: Value,
    timeout_secs: u64,
) -> Result<Value, String> {
    let data_dir = ledger_data_dir()?;
    std::fs::create_dir_all(data_dir.join("logs"))
        .map_err(|error| format!("Ledger could not create its private data directory: {error}"))?;

    // The engine ships as a PyInstaller onedir bundle inside Tauri's
    // resource directory (a onefile sidecar re-extracted ~85 MB on every
    // request, costing about four seconds per click). It is spawned fresh
    // per request and exits after one response, so no process outlives the
    // app and no listener is ever opened.
    let engine = app
        .path()
        .resolve("engine/ledger-engine.exe", BaseDirectory::Resource)
        .map_err(|error| format!("Ledger engine is missing: {error}"))?;
    if !engine.is_file() {
        return Err(format!(
            "Ledger engine is missing from the installation: {}",
            engine.display()
        ));
    }
    let command = app
        .shell()
        .command(&engine)
        .env("LEDGER_DATA_DIR", &data_dir);
    let (mut events, mut child) = command
        .spawn()
        .map_err(|error| format!("Ledger engine could not start: {error}"))?;
    let request = json!({"action": action, "params": params});
    child
        .write((request.to_string() + "\n").as_bytes())
        .map_err(|error| format!("Ledger engine request failed: {error}"))?;

    let collect = async {
        let mut stdout = Vec::new();
        let mut stderr = Vec::new();
        while let Some(event) = events.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => stdout.extend(bytes),
                CommandEvent::Stderr(bytes) => stderr.extend(bytes),
                CommandEvent::Terminated(_) => break,
                _ => {}
            }
        }
        (stdout, stderr)
    };

    let (stdout, stderr) =
        match tokio::time::timeout(Duration::from_secs(timeout_secs), collect).await {
            Ok(output) => output,
            Err(_) => {
                let _ = child.kill();
                return Err(format!(
                    "Ledger engine did not respond within {timeout_secs} seconds."
                ));
            }
        };
    if stdout.is_empty() {
        let detail = String::from_utf8_lossy(&stderr).trim().to_string();
        return Err(if detail.is_empty() {
            "Ledger engine exited without a response.".to_string()
        } else {
            format!("Ledger engine failed: {detail}")
        });
    }
    decode_engine_response(&stdout)
}

#[tauri::command]
async fn get_home_summary(app: AppHandle, period_days: Option<i64>) -> Result<Value, String> {
    run_engine(
        &app, "home_summary", json!({"period_days": period_days.unwrap_or(30)}),
        ENGINE_TIMEOUT_SECS,
    ).await
}

#[tauri::command]
async fn list_accounts(app: AppHandle) -> Result<Value, String> {
    run_engine(&app, "list_accounts", json!({}), ENGINE_TIMEOUT_SECS).await
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct NewAccountSpec {
    name: String,
    account_type: String,
    #[serde(default)]
    institution: String,
    #[serde(default)]
    opening_balance: f64,
}

#[tauri::command]
async fn create_account(app: AppHandle, spec: NewAccountSpec) -> Result<Value, String> {
    run_engine(
        &app,
        "create_account",
        json!({
            "name": spec.name,
            "type": spec.account_type,
            "institution": spec.institution,
            "opening_balance": spec.opening_balance,
        }),
        ENGINE_TIMEOUT_SECS,
    )
    .await
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct AiSettingsSpec {
    enabled: Option<bool>,
    base_url: Option<String>,
    model: Option<String>,
    // Absent means "leave the stored key alone", so saving any other setting
    // cannot silently erase it.
    api_key: Option<String>,
    scope: Option<String>,
    months: Option<i64>,
    focus: Option<String>,
    style: Option<String>,
    essential_categories: Option<Vec<String>>,
}

#[tauri::command]
async fn get_ai_settings(app: AppHandle) -> Result<Value, String> {
    run_engine(&app, "ai_settings", json!({}), ENGINE_TIMEOUT_SECS).await
}

#[tauri::command]
async fn save_ai_settings(app: AppHandle, spec: AiSettingsSpec) -> Result<Value, String> {
    run_engine(
        &app,
        "save_ai_settings",
        json!({
            "enabled": spec.enabled,
            "base_url": spec.base_url,
            "model": spec.model,
            "api_key": spec.api_key,
            "scope": spec.scope,
            "months": spec.months,
            "focus": spec.focus,
            "style": spec.style,
            "essential_categories": spec.essential_categories,
        }),
        ENGINE_TIMEOUT_SECS,
    )
    .await
}

#[tauri::command]
async fn forget_ai_key(app: AppHandle) -> Result<Value, String> {
    run_engine(&app, "forget_ai_key", json!({}), ENGINE_TIMEOUT_SECS).await
}

#[tauri::command]
async fn ai_payload_preview(
    app: AppHandle,
    scope: Option<String>,
    months: Option<i64>,
) -> Result<Value, String> {
    run_engine(
        &app,
        "ai_payload_preview",
        json!({"scope": scope, "months": months}),
        ENGINE_TIMEOUT_SECS,
    )
    .await
}

#[tauri::command]
async fn ai_coaching_summary(app: AppHandle) -> Result<Value, String> {
    run_engine(
        &app,
        "ai_coaching_summary",
        json!({}),
        ENGINE_TIMEOUT_SECS,
    )
    .await
}

#[tauri::command]
async fn get_net_worth_trend(app: AppHandle) -> Result<Value, String> {
    run_engine(&app, "net_worth_trend", json!({}), ENGINE_TIMEOUT_SECS).await
}

/// Record or correct one month's reading. The month is the key on the Python
/// side, so saving August twice corrects August rather than adding a second.
#[tauri::command]
async fn save_net_worth_entry(
    app: AppHandle,
    month: String,
    cash: f64,
    investments: f64,
    other_assets: f64,
    liabilities: f64,
    note: Option<String>,
) -> Result<Value, String> {
    run_engine(
        &app,
        "save_net_worth_entry",
        json!({
            "month": month,
            "cash": cash,
            "investments": investments,
            "other_assets": other_assets,
            "liabilities": liabilities,
            "note": note,
        }),
        ENGINE_TIMEOUT_SECS,
    )
    .await
}

#[tauri::command]
async fn delete_net_worth_entry(app: AppHandle, month: String) -> Result<Value, String> {
    run_engine(
        &app,
        "delete_net_worth_entry",
        json!({ "month": month }),
        ENGINE_TIMEOUT_SECS,
    )
    .await
}

/// Where to write a full copy of the ledger. A local-first app that cannot
/// hand the data back is just a different silo, so this is a save dialog
/// rather than a hidden folder.
#[tauri::command]
async fn pick_export_file(app: AppHandle) -> Result<Option<String>, String> {
    let (tx, rx) = tokio::sync::oneshot::channel();
    app.dialog()
        .file()
        .add_filter("Comma separated values", &["csv"])
        .set_file_name("northstar-transactions.csv")
        .set_title("Export your transactions")
        .save_file(move |file| {
            let _ = tx.send(file);
        });
    let picked = rx
        .await
        .map_err(|_| "The export dialog was interrupted.".to_string())?;
    Ok(picked
        .and_then(|p| p.into_path().ok())
        .map(|p| p.to_string_lossy().into_owned()))
}

#[tauri::command]
async fn export_transactions(app: AppHandle, path: String) -> Result<Value, String> {
    run_engine(
        &app,
        "export_transactions",
        json!({ "path": path }),
        ENGINE_TIMEOUT_SECS,
    )
    .await
}

#[tauri::command]
async fn get_money_focus(app: AppHandle) -> Result<Value, String> {
    run_engine(&app, "money_focus", json!({}), ENGINE_TIMEOUT_SECS).await
}

#[tauri::command]
async fn save_money_focus(
    app: AppHandle,
    name: String,
    target_amount: f64,
    started_month: String,
    target_date: Option<String>,
    already_saved: Option<f64>,
    note: Option<String>,
) -> Result<Value, String> {
    run_engine(
        &app,
        "save_money_focus",
        json!({
            "name": name,
            "target_amount": target_amount,
            "started_month": started_month,
            "target_date": target_date,
            "already_saved": already_saved,
            "note": note,
        }),
        ENGINE_TIMEOUT_SECS,
    )
    .await
}

#[tauri::command]
async fn clear_money_focus(app: AppHandle) -> Result<Value, String> {
    run_engine(&app, "clear_money_focus", json!({}), ENGINE_TIMEOUT_SECS).await
}

#[tauri::command]
async fn delete_transaction(app: AppHandle, id: i64) -> Result<Value, String> {
    run_engine(
        &app,
        "delete_transaction",
        json!({ "id": id }),
        ENGINE_TIMEOUT_SECS,
    )
    .await
}

#[tauri::command]
async fn get_insight_feed(app: AppHandle) -> Result<Value, String> {
    run_engine(&app, "insight_feed", json!({}), ENGINE_TIMEOUT_SECS).await
}

#[tauri::command]
async fn get_spending_patterns(app: AppHandle, months: Option<i64>) -> Result<Value, String> {
    run_engine(
        &app,
        "spending_patterns",
        json!({ "months": months }),
        ENGINE_TIMEOUT_SECS,
    )
    .await
}

#[tauri::command]
async fn explain_insight(app: AppHandle, finding_id: String) -> Result<Value, String> {
    // A provider call can be slow, so give it the import-length budget.
    run_engine(
        &app,
        "explain_insight",
        json!({ "finding_id": finding_id }),
        IMPORT_TIMEOUT_SECS,
    )
    .await
}

#[tauri::command]
async fn ai_ask(app: AppHandle, question: String) -> Result<Value, String> {
    // A provider call can be slow, so give it the import-length budget.
    run_engine(&app, "ai_ask", json!({"question": question}), IMPORT_TIMEOUT_SECS).await
}

#[tauri::command]
async fn test_ai_connection(app: AppHandle) -> Result<Value, String> {
    // This sends a fixed health-check phrase only, never financial data.
    run_engine(
        &app,
        "test_ai_connection",
        json!({}),
        IMPORT_TIMEOUT_SECS,
    )
    .await
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct SpendingAvailabilitySpec {
    account_id: i64,
    available: bool,
}

#[tauri::command]
async fn set_account_spending_availability(
    app: AppHandle,
    spec: SpendingAvailabilitySpec,
) -> Result<Value, String> {
    run_engine(
        &app,
        "set_account_spending_availability",
        json!({
            "account_id": spec.account_id,
            "available": spec.available,
        }),
        ENGINE_TIMEOUT_SECS,
    )
    .await
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct PreviewFileSpec {
    path: String,
    account_id: Option<i64>,
    mapping: Option<Value>,
    /// Set when the user resolves a date column that could be read two ways.
    date_format: Option<String>,
    /// Set when the user names their bank rather than mapping columns.
    bank: Option<String>,
    /// User decision when some, but not all, rows match an earlier import.
    import_mode: Option<String>,
}

#[tauri::command]
async fn preview_import(
    app: AppHandle,
    paths: Vec<String>,
    files: Option<Vec<PreviewFileSpec>>,
) -> Result<Value, String> {
    let files = files.map(|items| items.into_iter().map(|f| json!({
        "path": f.path,
        "account_id": f.account_id,
        "mapping": f.mapping,
        "date_format": f.date_format,
        "bank": f.bank,
        "import_mode": f.import_mode,
    })).collect::<Vec<_>>());
    run_engine(
        &app,
        "preview_import",
        json!({ "paths": paths, "files": files }),
        IMPORT_TIMEOUT_SECS,
    )
    .await
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct CsvMappingSpec {
    path: String,
    account_id: i64,
    mapping: Value,
    save_profile: bool,
    profile_name: String,
}

#[tauri::command]
async fn apply_csv_mapping(
    app: AppHandle, spec: CsvMappingSpec,
) -> Result<Value, String> {
    run_engine(
        &app,
        "apply_csv_mapping",
        json!({
            "path": spec.path,
            "account_id": spec.account_id,
            "mapping": spec.mapping,
            "save_profile": spec.save_profile,
            "profile_name": spec.profile_name,
        }),
        IMPORT_TIMEOUT_SECS,
    ).await
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct ConfirmFileSpec {
    path: String,
    account_id: i64,
    mapping: Option<Value>,
    date_format: Option<String>,
    bank: Option<String>,
    import_mode: Option<String>,
}

#[tauri::command]
async fn confirm_import(app: AppHandle, files: Vec<ConfirmFileSpec>) -> Result<Value, String> {
    let files: Vec<Value> = files
        .into_iter()
        .map(|f| json!({
            "path": f.path,
            "account_id": f.account_id,
            "mapping": f.mapping,
            "date_format": f.date_format,
            "bank": f.bank,
            "import_mode": f.import_mode,
        }))
        .collect();
    run_engine(
        &app,
        "confirm_import",
        json!({ "files": files }),
        IMPORT_TIMEOUT_SECS,
    )
    .await
}

#[tauri::command]
async fn reset_csv_profile(app: AppHandle, path: String) -> Result<Value, String> {
    run_engine(
        &app, "reset_csv_profile", json!({"path": path}), ENGINE_TIMEOUT_SECS,
    ).await
}

#[tauri::command]
async fn list_transactions(
    app: AppHandle,
    query: Option<TransactionQuery>,
) -> Result<Value, String> {
    let query = query.unwrap_or_default();
    run_engine(
        &app,
        "list_transactions",
        json!({
            "account_ref": query.account_ref,
            "search": query.search,
            "category": query.category,
            "direction": query.direction,
            "start_date": query.start_date,
            "end_date": query.end_date,
            "sort": query.sort,
            "descending": query.descending,
            "offset": query.offset,
            "limit": query.limit,
            "flagged_only": query.flagged_only,
            "suggested_only": query.suggested_only,
            "quick_review": query.quick_review,
            "cashflow_role": query.cashflow_role,
        }),
        ENGINE_TIMEOUT_SECS,
    )
    .await
}

#[derive(Default, Deserialize)]
#[serde(rename_all = "camelCase")]
struct TransactionQuery {
    account_ref: Option<i64>,
    #[serde(default)]
    search: String,
    #[serde(default)]
    category: String,
    #[serde(default)]
    direction: String,
    #[serde(default)]
    start_date: String,
    #[serde(default)]
    end_date: String,
    #[serde(default)]
    sort: String,
    #[serde(default = "default_true")]
    descending: bool,
    #[serde(default)]
    offset: i64,
    limit: Option<i64>,
    #[serde(default)]
    flagged_only: bool,
    #[serde(default)]
    suggested_only: bool,
    #[serde(default)]
    quick_review: bool,
    #[serde(default)]
    cashflow_role: String,
}

fn default_true() -> bool {
    true
}

#[tauri::command]
async fn correct_transaction(
    app: AppHandle,
    id: i64,
    category: String,
    note: Option<String>,
    apply_to_matching: bool,
    remember_rule: bool,
    transaction_type: Option<String>,
    shared_expense_override: Option<bool>,
    shared_user_share_pct: Option<f64>,
) -> Result<Value, String> {
    run_engine(
        &app,
        "correct_transaction",
        json!({
            "id": id,
            "category": category,
            "note": note,
            "apply_to_matching": apply_to_matching,
            "remember_rule": remember_rule,
            "transaction_type": transaction_type,
            "shared_expense_override": shared_expense_override,
            "shared_user_share_pct": shared_user_share_pct,
        }),
        ENGINE_TIMEOUT_SECS,
    )
    .await
}

#[tauri::command]
async fn undo_last_category_change(app: AppHandle) -> Result<Value, String> {
    run_engine(&app, "undo_last_category_change", json!({}), ENGINE_TIMEOUT_SECS).await
}

#[tauri::command]
async fn get_review_summary(app: AppHandle) -> Result<Value, String> {
    run_engine(&app, "review_summary", json!({}), ENGINE_TIMEOUT_SECS).await
}

#[tauri::command]
async fn get_shared_settings(app: AppHandle) -> Result<Value, String> {
    run_engine(&app, "shared_settings", json!({}), ENGINE_TIMEOUT_SECS).await
}

#[tauri::command]
async fn save_shared_settings(
    app: AppHandle, shared_with_name: String, default_user_share_pct: f64,
) -> Result<Value, String> {
    run_engine(&app, "save_shared_settings", json!({
        "shared_with_name": shared_with_name,
        "default_user_share_pct": default_user_share_pct,
    }), ENGINE_TIMEOUT_SECS).await
}

#[tauri::command]
async fn set_shared_rule(
    app: AppHandle, scope_type: String, scope_value: String,
    user_share_pct: f64, enabled: bool,
) -> Result<Value, String> {
    run_engine(&app, "set_shared_rule", json!({
        "scope_type": scope_type, "scope_value": scope_value,
        "user_share_pct": user_share_pct, "enabled": enabled,
    }), ENGINE_TIMEOUT_SECS).await
}

#[tauri::command]
async fn get_category_settings(app: AppHandle) -> Result<Value, String> {
    run_engine(&app, "category_settings", json!({}), ENGINE_TIMEOUT_SECS).await
}

#[tauri::command]
async fn update_category_rule(
    app: AppHandle, id: i64, category: String, enabled: bool,
) -> Result<Value, String> {
    run_engine(
        &app, "update_category_rule",
        json!({"id": id, "category": category, "enabled": enabled}),
        ENGINE_TIMEOUT_SECS,
    ).await
}

#[tauri::command]
async fn delete_category_rule(app: AppHandle, id: i64) -> Result<Value, String> {
    run_engine(
        &app, "delete_category_rule", json!({"id": id}), ENGINE_TIMEOUT_SECS,
    ).await
}

#[tauri::command]
async fn set_recurring_preference(
    app: AppHandle, merchant_normalized: String, status: String,
    display_name: Option<String>, category: Option<String>,
    apply_category: Option<bool>,
) -> Result<Value, String> {
    run_engine(
        &app, "set_recurring_preference",
        json!({"merchant_normalized": merchant_normalized, "status": status,
               "display_name": display_name.unwrap_or_default(),
               "category": category.unwrap_or_default(),
               "apply_category": apply_category.unwrap_or(false)}),
        ENGINE_TIMEOUT_SECS,
    ).await
}

#[tauri::command]
async fn set_income_source_preference(
    app: AppHandle, source_normalized: String, status: String,
) -> Result<Value, String> {
    run_engine(
        &app, "set_income_source_preference",
        json!({"source_normalized": source_normalized, "status": status}),
        ENGINE_TIMEOUT_SECS,
    ).await
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct ManualTransactionSpec {
    account_id: i64,
    date: String,
    description: String,
    #[serde(default)]
    merchant: String,
    amount: f64,
    direction: String,
    category: String,
    #[serde(default)]
    note: String,
    /// Keep a row identical to one already stored; the user confirmed it.
    #[serde(default)]
    allow_duplicate: bool,
}

#[tauri::command]
async fn add_manual_transaction(
    app: AppHandle,
    spec: ManualTransactionSpec,
) -> Result<Value, String> {
    run_engine(
        &app,
        "add_manual_transaction",
        json!({
            "account_id": spec.account_id,
            "date": spec.date,
            "description": spec.description,
            "merchant": spec.merchant,
            "amount": spec.amount,
            "direction": spec.direction,
            "category": spec.category,
            "note": spec.note,
            "allow_duplicate": spec.allow_duplicate,
        }),
        ENGINE_TIMEOUT_SECS,
    )
    .await
}

#[tauri::command]
async fn get_plan_summary(app: AppHandle) -> Result<Value, String> {
    run_engine(&app, "plan_summary", json!({}), ENGINE_TIMEOUT_SECS).await
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct PlanSpec {
    month: String,
    mode: String,
    income_target: f64,
    fixed_obligations: f64,
    flexible_allowance: f64,
    savings_target: f64,
    safety_buffer: f64,
    #[serde(default)]
    notes: String,
    #[serde(default)]
    fixed_override_reason: String,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct PlanPreviewSpec {
    mode: String,
    income_target: f64,
    fixed_obligations: f64,
    savings_target: f64,
    safety_buffer: f64,
    #[serde(default)]
    apply_preset: bool,
    #[serde(default)]
    apply_preference: bool,
    #[serde(default)]
    savings_preference_style: String,
    #[serde(default)]
    savings_preference_value: f64,
}

#[tauri::command]
async fn preview_plan(app: AppHandle, spec: PlanPreviewSpec) -> Result<Value, String> {
    run_engine(
        &app, "preview_plan", json!({
            "mode": spec.mode,
            "income_target": spec.income_target,
            "fixed_obligations": spec.fixed_obligations,
            "savings_target": spec.savings_target,
            "safety_buffer": spec.safety_buffer,
            "apply_preset": spec.apply_preset,
            "apply_preference": spec.apply_preference,
            "savings_preference_style": spec.savings_preference_style,
            "savings_preference_value": spec.savings_preference_value,
        }), ENGINE_TIMEOUT_SECS,
    ).await
}

#[tauri::command]
async fn save_plan(app: AppHandle, spec: PlanSpec) -> Result<Value, String> {
    run_engine(
        &app,
        "save_plan",
        json!({
            "month": spec.month,
            "mode": spec.mode,
            "income_target": spec.income_target,
            "fixed_obligations": spec.fixed_obligations,
            "flexible_allowance": spec.flexible_allowance,
            "savings_target": spec.savings_target,
            "safety_buffer": spec.safety_buffer,
            "notes": spec.notes,
            "fixed_override_reason": spec.fixed_override_reason,
        }),
        ENGINE_TIMEOUT_SECS,
    )
    .await
}

#[tauri::command]
async fn get_goals_summary(app: AppHandle) -> Result<Value, String> {
    run_engine(&app, "goals_summary", json!({}), ENGINE_TIMEOUT_SECS).await
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct GoalSpec {
    name: String,
    goal_type: String,
    target_amount: f64,
    #[serde(default)]
    current_amount: f64,
    #[serde(default)]
    target_date: String,
    #[serde(default)]
    monthly_contribution: f64,
    #[serde(default = "default_monthly")]
    contribution_frequency: String,
    #[serde(default = "default_true")]
    include_in_plan: bool,
    #[serde(default = "default_true")]
    show_milestones: bool,
    #[serde(default)]
    notes: String,
    #[serde(default = "default_manual")]
    progress_method: String,
    linked_account_ref: Option<i64>,
}

fn default_manual() -> String { "manual".to_string() }
fn default_monthly() -> String { "monthly".to_string() }

#[tauri::command]
async fn create_goal(app: AppHandle, spec: GoalSpec) -> Result<Value, String> {
    run_engine(
        &app,
        "create_goal",
        json!({
            "name": spec.name,
            "type": spec.goal_type,
            "target_amount": spec.target_amount,
            "current_amount": spec.current_amount,
            "target_date": spec.target_date,
            "monthly_contribution": spec.monthly_contribution,
            "contribution_frequency": spec.contribution_frequency,
            "include_in_plan": spec.include_in_plan,
            "show_milestones": spec.show_milestones,
            "notes": spec.notes,
            "progress_method": spec.progress_method,
            "linked_account_ref": spec.linked_account_ref,
        }),
        ENGINE_TIMEOUT_SECS,
    )
    .await
}


#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct GoalUpdateSpec {
    goal_id: i64,
    name: String,
    goal_type: String,
    target_amount: f64,
    #[serde(default)]
    target_date: String,
    #[serde(default)]
    monthly_contribution: f64,
    #[serde(default = "default_monthly")]
    contribution_frequency: String,
    #[serde(default = "default_true")]
    include_in_plan: bool,
    #[serde(default = "default_true")]
    show_milestones: bool,
    #[serde(default)]
    notes: String,
    #[serde(default = "default_manual")]
    progress_method: String,
    linked_account_ref: Option<i64>,
    status: String,
}

#[tauri::command]
async fn update_goal(app: AppHandle, spec: GoalUpdateSpec) -> Result<Value, String> {
    run_engine(&app, "update_goal", json!({
        "goal_id": spec.goal_id, "name": spec.name, "type": spec.goal_type,
        "target_amount": spec.target_amount, "target_date": spec.target_date,
        "monthly_contribution": spec.monthly_contribution,
        "contribution_frequency": spec.contribution_frequency,
        "include_in_plan": spec.include_in_plan, "notes": spec.notes,
        "show_milestones": spec.show_milestones,
        "progress_method": spec.progress_method,
        "linked_account_ref": spec.linked_account_ref, "status": spec.status,
    }), ENGINE_TIMEOUT_SECS).await
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct ContributionSpec {
    goal_id: i64,
    amount: f64,
    #[serde(default)]
    date: String,
    #[serde(default)]
    note: String,
}

#[tauri::command]
async fn contribute_goal(app: AppHandle, spec: ContributionSpec) -> Result<Value, String> {
    run_engine(
        &app,
        "contribute_goal",
        json!({
            "goal_id": spec.goal_id,
            "amount": spec.amount,
            "date": spec.date,
            "note": spec.note,
        }),
        ENGINE_TIMEOUT_SECS,
    )
    .await
}

#[tauri::command]
async fn get_insights_summary(
    app: AppHandle, period_days: Option<i64>, share_view: Option<String>,
) -> Result<Value, String> {
    run_engine(
        &app, "insights_summary",
        json!({"period_days": period_days.unwrap_or(30),
               "share_view": share_view.unwrap_or_else(|| "personal".to_string())}),
        ENGINE_TIMEOUT_SECS,
    ).await
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct QuickNetWorthSpec {
    total_assets: f64,
    total_liabilities: f64,
    as_of_date: String,
    period_days: Option<i64>,
}

#[tauri::command]
async fn save_quick_net_worth(
    app: AppHandle, spec: QuickNetWorthSpec,
) -> Result<Value, String> {
    run_engine(&app, "save_quick_net_worth", json!({
        "total_assets": spec.total_assets,
        "total_liabilities": spec.total_liabilities,
        "as_of_date": spec.as_of_date,
        "period_days": spec.period_days.unwrap_or(30),
    }), ENGINE_TIMEOUT_SECS).await
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct BalanceSpec {
    account_ref: i64,
    balance: f64,
    as_of_date: String,
    #[serde(default)]
    note: String,
    period_days: Option<i64>,
}

#[tauri::command]
async fn add_account_balance(app: AppHandle, spec: BalanceSpec) -> Result<Value, String> {
    run_engine(
        &app,
        "add_account_balance",
        json!({
            "account_ref": spec.account_ref,
            "balance": spec.balance,
            "as_of_date": spec.as_of_date,
            "note": spec.note,
            "period_days": spec.period_days.unwrap_or(30),
        }),
        ENGINE_TIMEOUT_SECS,
    ).await
}

#[tauri::command]
async fn get_home_dashboard(app: AppHandle, period_days: Option<i64>) -> Result<Value, String> {
    run_engine(
        &app, "home_dashboard",
        json!({"period_days": period_days.unwrap_or(30)}), ENGINE_TIMEOUT_SECS,
    ).await
}

#[tauri::command]
async fn get_backup_status(app: AppHandle) -> Result<Value, String> {
    run_engine(&app, "backup_status", json!({}), ENGINE_TIMEOUT_SECS).await
}

#[tauri::command]
async fn create_backup(app: AppHandle) -> Result<Value, String> {
    run_engine(&app, "create_backup", json!({}), ENGINE_TIMEOUT_SECS).await
}

#[tauri::command]
async fn restore_backup(app: AppHandle, path: String) -> Result<Value, String> {
    run_engine(
        &app,
        "restore_backup",
        json!({"path": path}),
        IMPORT_TIMEOUT_SECS,
    )
    .await
}

#[tauri::command]
async fn get_data_safety_status(app: AppHandle) -> Result<Value, String> {
    run_engine(&app, "data_safety_status", json!({}), ENGINE_TIMEOUT_SECS).await
}

#[tauri::command]
async fn preview_legacy_repair(app: AppHandle, paths: Vec<String>) -> Result<Value, String> {
    run_engine(
        &app, "preview_legacy_repair", json!({"paths": paths}),
        IMPORT_TIMEOUT_SECS,
    ).await
}

#[tauri::command]
async fn repair_legacy_data(app: AppHandle, paths: Vec<String>) -> Result<Value, String> {
    run_engine(
        &app, "repair_legacy_data", json!({"paths": paths}),
        IMPORT_TIMEOUT_SECS,
    ).await
}

#[tauri::command]
async fn reset_financial_data(
    app: AppHandle,
    confirmation: String,
    complete_reset: bool,
) -> Result<Value, String> {
    run_engine(
        &app, "reset_financial_data",
        json!({"confirmation": confirmation, "complete_reset": complete_reset}),
        IMPORT_TIMEOUT_SECS,
    ).await
}

/// Native file picker for statement files. The webview itself has no
/// filesystem or dialog permission — the dialog runs in Rust, and only the
/// user's explicit selection is returned.
///
/// Deliberately async with the callback API: a sync command holding
/// `blocking_pick_files` was measured freezing the main thread (the whole
/// window stopped responding while the dialog was open).
#[tauri::command]
async fn pick_import_files(app: AppHandle) -> Result<Vec<String>, String> {
    let (tx, rx) = tokio::sync::oneshot::channel();
    app.dialog()
        .file()
        .add_filter("Bank statements", &["pdf", "csv"])
        .set_title("Choose statement files to import")
        .pick_files(move |files| {
            let _ = tx.send(files);
        });
    let picked = rx
        .await
        .map_err(|_| "The file dialog was interrupted.".to_string())?;
    Ok(picked
        .unwrap_or_default()
        .into_iter()
        .filter_map(|p| p.into_path().ok())
        .map(|p| p.to_string_lossy().into_owned())
        .collect())
}

#[tauri::command]
async fn pick_backup_file(app: AppHandle) -> Result<Option<String>, String> {
    let (tx, rx) = tokio::sync::oneshot::channel();
    app.dialog()
        .file()
        .add_filter("Northstar Ledger backup", &["db"])
        .set_title("Choose a Northstar Ledger backup to restore")
        .pick_file(move |file| {
            let _ = tx.send(file);
        });
    let picked = rx
        .await
        .map_err(|_| "The backup file dialog was interrupted.".to_string())?;
    Ok(picked
        .and_then(|p| p.into_path().ok())
        .map(|p| p.to_string_lossy().into_owned()))
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            get_ai_settings,
            save_ai_settings,
            forget_ai_key,
            ai_payload_preview,
            ai_coaching_summary,
            get_insight_feed,
            get_spending_patterns,
            explain_insight,
            get_net_worth_trend,
            save_net_worth_entry,
            delete_net_worth_entry,
            get_money_focus,
            save_money_focus,
            clear_money_focus,
            pick_export_file,
            export_transactions,
            delete_transaction,
            ai_ask,
            test_ai_connection,
            get_home_summary,
            list_accounts,
            create_account,
            set_account_spending_availability,
            preview_import,
            apply_csv_mapping,
            confirm_import,
            reset_csv_profile,
            list_transactions,
            correct_transaction,
            undo_last_category_change,
            get_review_summary,
            get_shared_settings,
            save_shared_settings,
            set_shared_rule,
            get_category_settings,
            update_category_rule,
            delete_category_rule,
            set_recurring_preference,
            set_income_source_preference,
            add_manual_transaction,
            get_plan_summary,
            preview_plan,
            save_plan,
            get_goals_summary,
            create_goal,
            update_goal,
            contribute_goal,
            get_insights_summary,
            save_quick_net_worth,
            add_account_balance,
            get_home_dashboard,
            get_backup_status,
            create_backup,
            restore_backup,
            get_data_safety_status,
            preview_legacy_repair,
            repair_legacy_data,
            reset_financial_data,
            pick_import_files,
            pick_backup_file
        ])
        .run(tauri::generate_context!())
        .expect("error while running Ledger");
}

#[cfg(test)]
mod tests {
    use super::decode_engine_response;

    #[test]
    fn extracts_home_data_from_success_response() {
        let value = decode_engine_response(br#"{"ok":true,"data":{"generated_for":"2026-07-18"}}"#)
            .expect("valid response");
        assert_eq!(value["generated_for"], "2026-07-18");
    }

    #[test]
    fn preserves_engine_error_message() {
        let error = decode_engine_response(br#"{"ok":false,"error":"boom"}"#)
            .expect_err("failure should propagate");
        assert_eq!(error, "boom");
    }

    #[test]
    fn missing_data_is_an_error() {
        let error =
            decode_engine_response(br#"{"ok":true}"#).expect_err("missing data should error");
        assert!(error.contains("no data"));
    }

    /// A command has to be declared in three places to work: `generate_handler!`
    /// registers it, `build.rs` makes tauri-build generate an "allow-" permission
    /// for it, and `capabilities/main.json` grants that permission to the window.
    /// Miss the last two and the code still compiles; the command is simply
    /// rejected by the ACL when a user clicks the button. That is how the AI
    /// panel and "forget this file's saved layout" both shipped broken. These
    /// tests compare the three lists so the next miss fails here instead.
    fn quoted_names(block: &str) -> Vec<String> {
        block
            .split('"')
            .skip(1)
            .step_by(2)
            .map(str::to_string)
            .collect()
    }

    fn registered_commands() -> Vec<String> {
        let source = include_str!("main.rs");
        source
            .split_once("generate_handler![")
            .expect("generate_handler! block")
            .1
            .split_once(']')
            .expect("end of handler list")
            .0
            .split(',')
            .map(|name| name.trim().to_string())
            .filter(|name| !name.is_empty())
            .collect()
    }

    fn declared_commands() -> Vec<String> {
        let build = include_str!("../build.rs");
        let block = build
            .split_once(".commands(&[")
            .expect("commands list in build.rs")
            .1
            .split_once("])")
            .expect("end of commands list")
            .0;
        quoted_names(block)
    }

    fn granted_permissions() -> Vec<String> {
        let capabilities = include_str!("../capabilities/main.json");
        let block = capabilities
            .split_once("\"permissions\"")
            .expect("permissions list")
            .1;
        quoted_names(block)
            .into_iter()
            .filter_map(|entry| entry.strip_prefix("allow-").map(str::to_string))
            .collect()
    }

    #[test]
    fn every_registered_command_is_declared_and_granted() {
        let declared = declared_commands();
        let granted = granted_permissions();
        let mut broken = Vec::new();
        for command in registered_commands() {
            if !declared.contains(&command) {
                broken.push(format!("{command}: missing from build.rs"));
            }
            if !granted.contains(&command.replace('_', "-")) {
                broken.push(format!("{command}: missing from capabilities/main.json"));
            }
        }
        assert!(broken.is_empty(), "commands the frontend cannot call: {broken:#?}");
    }

    #[test]
    fn nothing_is_granted_that_is_not_registered() {
        let registered: Vec<String> = registered_commands()
            .into_iter()
            .map(|name| name.replace('_', "-"))
            .collect();
        let stale: Vec<String> = granted_permissions()
            .into_iter()
            .filter(|permission| !registered.contains(permission))
            .collect();
        assert!(
            stale.is_empty(),
            "permissions granted for commands that no longer exist: {stale:?}"
        );
    }
}
