import { getVersion } from "@tauri-apps/api/app";
import { money } from "./money";
import { useCallback, useEffect, useMemo, useState } from "react";

const AI_CONSENT = `Turning this on sends your financial figures to an outside company.

Northstar is offline until you do this. After it, the data you selected leaves this computer every time you ask a question. The provider's terms govern what happens to it: they may log it, keep it, or train on it. Northstar cannot control that and is not responsible for it.

You can switch this off or forget your key at any time. A model running on this computer gives you the same feature with nothing leaving.

Turn on AI assistance?`;
import {
  addAccountBalance,
  createLocalBackup,
  deleteCategoryRule,
  loadPlanningBalances,
  setAccountSpendingAvailability,
  exportTransactions,
  forgetAiKey,
  loadAiSettings,
  loadCategorySettings,
  previewAiPayload,
  saveAiSettings,
  loadBackupStatus,
  loadDataSafetyStatus,
  pickBackupFile,
  pickExportFile,
  pickImportFiles,
  previewLegacyRepair,
  resetFinancialData,
  restoreLocalBackup,
  runLegacyRepair,
  setRecurringPreference,
  setIncomeSourcePreference,
  testAiConnection,
  updateCategoryRule,
} from "./api";
import {
  clearNorthstarPreferences, readAnalysisPeriod, readDensity,
  readHomeSecondary, readLandingPage, readShowNetWorth, readSavingsPreference,
  readWeekStart,
  saveAnalysisPeriod, saveDensity, saveHomeSecondary, saveLandingPage,
  saveShowNetWorth, saveSavingsPreference, saveWeekStart,
  type AnalysisPeriod, type Density, type LandingPage, type SavingsStyle,
  type WeekStart,
} from "./preferences";
import type {
  BackupPayload,
  DataSafetyPayload,
  LegacyRepairPreview,
  RepairResult,
  ResetResult,
  CategorySettingsPayload,
  AiSettings,
  AiPayloadPreview,
  PlanningBalances,
} from "./types";

interface Props {
  onDataChanged: () => void;
  focusAnchor?: string;
  focusToken?: number;
}

function fileName(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}

function SettingsView({ onDataChanged, focusAnchor, focusToken }: Props) {
  const [data, setData] = useState<BackupPayload | null>(null);
  const [safety, setSafety] = useState<DataSafetyPayload | null>(null);
  const [version, setVersion] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [pending, setPending] = useState("");
  const [repairPaths, setRepairPaths] = useState<string[]>([]);
  const [repairPreview, setRepairPreview] = useState<LegacyRepairPreview | null>(null);
  const [ai, setAi] = useState<AiSettings | null>(null);
  const [aiBase, setAiBase] = useState("");
  const [aiModel, setAiModel] = useState("");
  const [aiKey, setAiKey] = useState("");
  const [aiBusy, setAiBusy] = useState(false);
  const [aiError, setAiError] = useState("");
  const [aiConnection, setAiConnection] = useState("");
  const [aiPreview, setAiPreview] = useState<AiPayloadPreview | null>(null);

  // Which preset the saved address corresponds to, so the picker reflects
  // what is actually stored rather than a separate remembered choice.
  const chosenProvider = useMemo(() => {
    const providers = ai?.providers ?? [];
    const custom = providers[providers.length - 1]
      ?? { value: "custom", label: "Somewhere else", base_url: "", model: "", note: "" };
    const normalize = (url: string) => url.trim().replace(/\/+$/, "").toLowerCase();
    return providers.find(
      (p) => p.base_url && normalize(p.base_url) === normalize(aiBase),
    ) ?? custom;
  }, [ai, aiBase]);

  const applyAi = useCallback((next: AiSettings) => {
    setAi(next);
    setAiBase(next.base_url);
    setAiModel(next.model);
    // The key is never returned, so the field stays empty once stored.
    setAiKey("");
  }, []);

  const loadAi = useCallback(async () => {
    setAiError("");
    try {
      applyAi((await loadAiSettings()).ai);
    } catch (cause) {
      setAiError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [applyAi]);

  const saveAi = useCallback(async (spec: Parameters<typeof saveAiSettings>[0]) => {
    setAiBusy(true);
    setAiError("");
    try {
      applyAi((await saveAiSettings(spec)).ai);
      setAiPreview(null);
    } catch (cause) {
      setAiError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setAiBusy(false);
    }
  }, [applyAi]);

  const showPayload = useCallback(async () => {
    setAiBusy(true);
    setAiError("");
    try {
      setAiPreview((await previewAiPayload()).preview);
    } catch (cause) {
      setAiError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setAiBusy(false);
    }
  }, []);

  const saveAndTestAi = useCallback(async () => {
    setAiBusy(true);
    setAiError("");
    setAiConnection("");
    try {
      const saved = await saveAiSettings({
        baseUrl: aiBase,
        model: aiModel,
        apiKey: aiKey.trim() || undefined,
      });
      applyAi(saved.ai);
      const result = await testAiConnection();
      setAiConnection(result.message);
    } catch (cause) {
      setAiError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setAiBusy(false);
    }
  }, [aiBase, aiKey, aiModel, applyAi]);

  const dropKey = useCallback(async () => {
    setAiBusy(true);
    setAiError("");
    try {
      applyAi((await forgetAiKey()).ai);
      setAiPreview(null);
      setAiConnection("");
    } catch (cause) {
      setAiError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setAiBusy(false);
    }
  }, [applyAi]);
  const [repairResult, setRepairResult] = useState<RepairResult | null>(null);
  const [showReset, setShowReset] = useState(false);
  const [resetText, setResetText] = useState("");
  const [completeReset, setCompleteReset] = useState(false);
  const [resetResult, setResetResult] = useState<ResetResult | null>(null);
  const [categorySettings, setCategorySettings] = useState<CategorySettingsPayload | null>(null);
  const [defaultPeriod, setDefaultPeriod] = useState<AnalysisPeriod>(readAnalysisPeriod);
  const [density, setDensity] = useState<Density>(readDensity);
  const [showSecondary, setShowSecondary] = useState(readHomeSecondary);
  const [landingPage,setLandingPage]=useState<LandingPage>(readLandingPage);
  const [showNetWorth,setShowNetWorth]=useState(readShowNetWorth);
  const [savingsPreference,setSavingsPreference]=useState(readSavingsPreference);
  const [weekStart,setWeekStart]=useState<WeekStart>(readWeekStart);
  const [exporting,setExporting]=useState(false);
  const [exportNote,setExportNote]=useState("");
  const [exportError,setExportError]=useState("");
  const [recurringEdits,setRecurringEdits]=useState<Record<string,{name:string;category:string}>>({});
  const [saved,setSaved]=useState("");
  // Planning balances: what each account currently holds. Inputs to Safe to
  // Spend and Plan, never summed into a net-worth figure.
  const [planning,setPlanning]=useState<PlanningBalances|null>(null);
  const [planningBusy,setPlanningBusy]=useState(false);
  const [planningNote,setPlanningNote]=useState("");
  const [balanceForm,setBalanceForm]=useState({
    accountRef:0, balance:"",
    asOfDate:new Date().toISOString().slice(0,10), note:"",
  });

  useEffect(()=>{if(!focusAnchor)return;const timer=window.setTimeout(()=>{const target=document.getElementById(focusAnchor);if(!target)return;target.scrollIntoView({behavior:"smooth",block:"start"});
    // Scrolling a section into view is not the same as landing on the thing
    // the link was about. Home and Plan send people here to change one
    // control, so move the keyboard there too.
    const control=target.querySelector<HTMLElement>("select,input,button");
    control?.focus({preventScroll:true});},250);return()=>window.clearTimeout(timer);},[focusAnchor,focusToken,categorySettings,planning]);

  const refresh = useCallback(async () => {
    try {
      // Each finance request starts a short-lived sidecar that initializes
      // SQLite. Loading them concurrently can race on schema checks
      // and leave Settings showing "database is locked". Keep the lightweight
      // app-version lookup first, then read every finance packet in order.
      const appVersion = await getVersion();
      const aiStatus = await loadAiSettings();
      const status = await loadBackupStatus();
      const safetyStatus = await loadDataSafetyStatus();
      const categoryStatus = await loadCategorySettings();
      const planningStatus = await loadPlanningBalances();
      applyAi(aiStatus.ai);
      setPlanning(planningStatus);
      setBalanceForm(current => current.accountRef || !planningStatus.accounts.length
        ? current
        : { ...current, accountRef: planningStatus.accounts[0].id });
      setData(status);
      setVersion(appVersion);
      setSafety(safetyStatus);
      setCategorySettings(categoryStatus);
      setRecurringEdits(Object.fromEntries(categoryStatus.recurring.map(item=>{
        const pref=categoryStatus.recurring_preferences.find(p=>p.merchant_normalized===item.merchant_normalized);
        return [item.merchant_normalized,{name:pref?.display_name||item.merchant,category:pref?.category||item.category}];
      })));
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [applyAi]);

  const changePeriod = (value: AnalysisPeriod) => { setDefaultPeriod(value); saveAnalysisPeriod(value); };
  const changeDensity = (value: Density) => { setDensity(value); saveDensity(value); };
  const changeSecondary = (value: boolean) => { setShowSecondary(value); saveHomeSecondary(value); };
  const changeLanding=(value:LandingPage)=>{setLandingPage(value);saveLandingPage(value);};
  const changeNetWorth=(value:boolean)=>{setShowNetWorth(value);saveShowNetWorth(value);};
  const changeSavings=(style:SavingsStyle,value:number)=>{const next={style,value};setSavingsPreference(next);saveSavingsPreference(style,value);};
  const changeWeekStart=(value:WeekStart)=>{setWeekStart(value);saveWeekStart(value);};
  const saveBalance=async()=>{
    setPlanningBusy(true);setError("");setPlanningNote("");
    try{
      const next=await addAccountBalance({
        accountRef:balanceForm.accountRef, balance:Number(balanceForm.balance),
        asOfDate:balanceForm.asOfDate, note:balanceForm.note, periodDays:30,
      });
      setPlanning(next);
      setBalanceForm(current=>({...current,balance:"",note:""}));
      setPlanningNote("Balance recorded. Safe to Spend and Plan use it from now on.");
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause));}
    finally{setPlanningBusy(false);}
  };
  const changeSpendable=async(accountId:number,available:boolean)=>{
    setPlanningBusy(true);setError("");setPlanningNote("");
    try{
      const result=await setAccountSpendingAvailability(accountId,available);
      setPlanning(current=>current?{...current,accounts:result.accounts}:current);
      onDataChanged();
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause));}
    finally{setPlanningBusy(false);}
  };
  const runExport = async () => {
    setExportError(""); setExportNote("");
    const destination = await pickExportFile();
    // Cancelling the dialog is a decision, not a failure. Say nothing.
    if (!destination) return;
    setExporting(true);
    try {
      const result = await exportTransactions(destination);
      setExportNote(`${result.rows.toLocaleString()} transaction${result.rows === 1 ? "" : "s"} written to ${fileName(result.path)}.`);
    } catch (cause) {
      setExportError(cause instanceof Error ? cause.message : String(cause));
    } finally { setExporting(false); }
  };
  const editRule = async (id:number, category:string, enabled:boolean) => {
    setBusy(`rule-${id}`);
    try { setCategorySettings(await updateCategoryRule(id, category, enabled)); onDataChanged(); }
    catch(c){setError(c instanceof Error?c.message:String(c));} finally { setBusy(""); }
  };
  const removeRule = async (id:number) => {
    if (!window.confirm("Delete this merchant rule? Historical transactions will not change.")) return;
    setBusy(`rule-${id}`);
    try { await deleteCategoryRule(id); setCategorySettings(await loadCategorySettings()); onDataChanged(); }
    catch(c){setError(c instanceof Error?c.message:String(c));} finally { setBusy(""); }
  };
  const recurringStatus = (merchant:string, fallback:string) => categorySettings?.recurring_preferences.find(p=>p.merchant_normalized===merchant)?.status ?? fallback;
  const changeRecurring = async (merchant:string, status:string, saveDetails=false) => {
    setBusy(`recurring-${merchant}`);setSaved("");
    try { const edit=recurringEdits[merchant];const payload=await setRecurringPreference(merchant,status,saveDetails?edit?.name??"":"",saveDetails?edit?.category??"":"",saveDetails);setCategorySettings(payload); setSaved(`recurring-${merchant}`); onDataChanged(); }
    catch(c){setError(c instanceof Error?c.message:String(c));} finally { setBusy(""); }
  };
  const changeIncomeSource=async(source:string,status:"confirmed"|"excluded")=>{setBusy(`income-${source}`);setSaved("");try{setCategorySettings(await setIncomeSourcePreference(source,status));setSaved(`income-${source}`);onDataChanged();}catch(c){setError(c instanceof Error?c.message:String(c));}finally{setBusy("");}};

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const backup = async () => {
    setBusy("backup");
    try {
      setData(await createLocalBackup());
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  const choose = async () => {
    const path = await pickBackupFile();
    if (path) setPending(path);
  };

  const restore = async (path = pending) => {
    setBusy("restore");
    try {
      setData(await restoreLocalBackup(path));
      setPending("");
      setRepairResult(null);
      setResetResult(null);
      onDataChanged();
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  const chooseRepairSources = async () => {
    setBusy("repair-preview");
    setError("");
    try {
      const paths = await pickImportFiles();
      if (!paths.length) return;
      const preview = await previewLegacyRepair(paths);
      setRepairPaths(paths);
      setRepairPreview(preview);
    } catch (cause) {
      setRepairPaths([]);
      setRepairPreview(null);
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  const repair = async () => {
    setBusy("repair");
    setError("");
    try {
      const result = await runLegacyRepair(repairPaths);
      setRepairResult(result);
      setRepairPaths([]);
      setRepairPreview(null);
      onDataChanged();
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  const reset = async () => {
    setBusy("reset");
    setError("");
    try {
      const result = await resetFinancialData(resetText, completeReset);
      if (completeReset) clearNorthstarPreferences();
      setResetResult(result);
      setShowReset(false);
      setResetText("");
      setCompleteReset(false);
      onDataChanged();
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  return (
    <section className="workflow-panel">
      <div className="panel-head">
        <div>
          <span className="eyebrow">Settings</span>
          <h2>Local data and recovery</h2>
          <p>
            Northstar Ledger stores finance data on this computer and never
            requires an account.
          </p>
        </div>
      </div>
      {error && <div className="inline-error" role="alert">{error}</div>}
      <div className="settings-grid">
        <article className="form-card">
          <h3>About</h3>
          <dl>
            <div><dt>Product</dt><dd>Northstar Ledger {version || "…"}</dd></div>
            <div><dt>Storage</dt><dd>{data?.data_directory ?? "Loading…"}</dd></div>
            <div><dt>Database</dt><dd>{data?.database_name ?? "finance.db"}</dd></div>
            <div><dt>Finance data</dt><dd>Stays on this computer. Every calculation runs in the local sidecar.</dd></div>
            <div><dt>Network</dt><dd>Nothing is sent unless you ask. AI assistance contacts the provider you configure; checking for updates contacts GitHub. Both are off until you act.</dd></div>
          </dl>
        </article>
        <article className="form-card">
          <h3>Backups</h3>
          <p className="guidance">
            Create a validated SQLite recovery point before major changes.
          </p>
          <div className="button-row">
            <button disabled={!!busy} onClick={() => void backup()}>
              {busy === "backup" ? "Backing up…" : "Create backup"}
            </button>
            <button className="ghost-button" disabled={!!busy} onClick={() => void choose()}>
              Restore from backup…
            </button>
          </div>
          {data?.created && <p className="success-text">Created {data.created}</p>}
          {data?.restored && (
            <p className="success-text">
              Restored {data.restored}. A pre-restore rollback copy was kept.
            </p>
          )}
        </article>
      </div>

      <article className="form-card" id="planning-balances">
        <h3>Planning balances</h3>
        <p className="guidance">
          What each account holds right now. Safe to Spend and Plan use these
          figures. They are not your net worth: that is the monthly reading you
          enter under Insights, and nothing here changes it or adds to it.
        </p>
        {planning?.balances.length ? (
          <div className="settings-list">
            {planning.balances.map((b) => (
              <div className="rank-row" key={`${b.account_name}-${b.account_kind}`}>
                <span>
                  {b.account_name}
                  <small>{b.account_kind} · as of {b.as_of_date}</small>
                </span>
                <strong>{money(b.balance)}</strong>
              </div>
            ))}
          </div>
        ) : (
          <p className="file-meta">
            No balances recorded yet. Northstar never infers one from your
            transactions, so Safe to Spend stays unavailable until you add one.
          </p>
        )}
        <h4>Add or update a balance</h4>
        <div className="form-grid">
          <label>Northstar account
            <select value={balanceForm.accountRef || ""}
              onChange={(e)=>setBalanceForm({...balanceForm,accountRef:Number(e.target.value)})}>
              <option value="">Choose an account…</option>
              {(planning?.accounts ?? []).map((account)=>(
                <option key={account.id} value={account.id}>
                  {account.name} · {account.type_label}
                </option>
              ))}
            </select>
          </label>
          <label>Current balance
            <input type="number" min="0" step="0.01" value={balanceForm.balance}
              onChange={(e)=>setBalanceForm({...balanceForm,balance:e.target.value})} />
          </label>
          <label>As of
            <input type="date" value={balanceForm.asOfDate}
              onChange={(e)=>setBalanceForm({...balanceForm,asOfDate:e.target.value})} />
          </label>
          <label>Note (optional)
            <input value={balanceForm.note}
              onChange={(e)=>setBalanceForm({...balanceForm,note:e.target.value})} />
          </label>
        </div>
        {!planning?.accounts.length && (
          <p className="warning-text">
            Create or import into an account before adding a balance.
          </p>
        )}
        <button type="button"
          disabled={planningBusy || !balanceForm.accountRef || !balanceForm.balance}
          onClick={()=>void saveBalance()}>
          {planningBusy ? "Saving…" : "Save balance snapshot"}
        </button>
        {planningNote && <p className="success-text">{planningNote}</p>}
      </article>

      <article className="form-card" id="spendable-accounts">
        <h3>Accounts available for spending</h3>
        <p className="guidance">
          Chequing and cash start included. Savings and investments stay
          protected unless you choose otherwise. This only decides what Safe to
          Spend may draw on.
        </p>
        <div className="settings-list">
          {(planning?.accounts ?? [])
            .filter(account=>!["credit_card","loan","mortgage","other_liability"].includes(account.type))
            .map(account=>(
              <label className="setting-row" key={account.id}>
                <span>
                  <strong>{account.name}</strong>
                  <small>
                    {account.type_label}
                    {account.available_for_spending
                      ? " · included in Safe to Spend"
                      : " · protected from Safe to Spend"}
                  </small>
                </span>
                <input type="checkbox" checked={account.available_for_spending}
                  disabled={planningBusy}
                  onChange={event=>void changeSpendable(account.id,event.target.checked)} />
              </label>
            ))}
        </div>
        {!planning?.accounts.length && (
          <p className="file-meta">No accounts yet.</p>
        )}
      </article>

      <h3 className="insight-section" id="ai-assist">
        AI assistance <em className="beta-tag">Beta</em>
      </h3>
      <p className="chart-explainer">
        Optional, and off unless you turn it on. None of the finance
        math depends on it: every figure on every screen is computed
        locally whether AI is enabled or not. With an online provider
        enabled, the evidence packet for the question you asked is sent
        to that provider. Provider compatibility varies, which is why
        this is marked Beta. AI can never edit your financial records.
      </p>
      <div className="settings-grid">
        {/* This panel is the longest thing in Settings and it was sitting in
            one half of a two-column grid, so the explanation ran narrow and
            the other half stayed empty. It spans the row now: what turning it
            on means on the left, the connection itself on the right. */}
        <article className="form-card ai-card settings-wide">
          <h3>Ask an AI about your finances</h3>
          <div className="ai-columns">
          <div>
          <div className="privacy-statement">
            <strong>What this changes</strong>
            <p>
              With this off, Northstar is entirely offline: your statements,
              figures and settings never leave this computer, and the app makes
              no network requests at all.
            </p>
            <p>
              With it on, and pointed at an online provider, the data you choose
              below is sent to that provider each time you ask a question. What
              they do with it is governed by their terms, not Northstar&apos;s.
              They may log it, retain it, or use it to train on. Northstar
              cannot control that and is not responsible for it. You supply the
              key, you pay for the usage, and you can switch this off or forget
              the key at any time.
            </p>
            <p>
              Pointing it at a model running on this computer keeps everything
              local and still gives you the feature.
            </p>
          </div>
          <p className="guidance">
            {ai?.is_local
              // Repeating the "your data leaves" warning while pointed at
              // localhost would train people to ignore it.
              ? `Ask questions about your own figures. Pointed at a model on
                 this computer, nothing leaves it, so this works like the rest
                 of Northstar. It is off until you switch it on.`
              : `Everything else in Northstar happens on this computer. This
                 does not. Turning it on sends your figures to whichever
                 provider you point it at, using a key you supply and pay for.
                 It is off until you switch it on, and nothing is sent while
                 it is off.`}
          </p>
          {!ai ? (
            // A panel that only ever says "Loading…" is how this shipped
            // broken: the settings request was rejected and the reason was
            // never shown. Say what went wrong and offer another go.
            aiError ? (
              <>
                <div className="inline-error">
                  AI assistance could not be loaded. {aiError}
                </div>
                <div className="button-row">
                  <button type="button" className="ghost-button"
                    onClick={() => void loadAi()}>Try again</button>
                </div>
              </>
            ) : <p className="guidance">Loading…</p>
          ) : <>
            {(() => {
              const ready = ai.is_local || ai.api_key_set || aiKey.trim();
              return (
                <label className={`toggle-row${ready ? "" : " toggle-row-disabled"}`}>
                  <span className="toggle-copy">
                    <strong>Enable AI assistance</strong>
                    <small>{ready
                      ? ai.enabled
                        ? "On. Northstar may send the selected data when you ask a question."
                        : "Off. Nothing is sent to the provider."
                      : "Choose a provider and add a key first."}</small>
                  </span>
                  <span className="toggle-control">
                    <input type="checkbox" checked={ai.enabled}
                      aria-label="Enable AI assistance"
                      className="toggle-input"
                    disabled={aiBusy || !ready}
                    onChange={(e) => {
                      // Switching this on is the one decision in Northstar
                      // that changes where someone's data lives. It gets an
                      // explicit yes rather than a silent toggle.
                      if (e.target.checked && !ai.is_local
                          && !window.confirm(AI_CONSENT)) {
                        return;
                      }
                      void saveAi({ enabled: e.target.checked });
                    }} />
                    <span className="toggle-track" aria-hidden="true">
                      <span className="toggle-thumb" />
                    </span>
                  </span>
                </label>
              );
            })()}
            {ai.model_migrated_from && (
              <p className="success-text">
                Northstar updated the retired MiniMax model
                {" "}{ai.model_migrated_from} to MiniMax-M3.
              </p>
            )}
            {ai.is_local && (
              <p className="success-text">
                This address is on your own computer, so your figures do not
                leave it. No key needed.
              </p>
            )}
            {ai.key_error && <div className="inline-error">{ai.key_error}</div>}
          </>}
          </div>
          <div>
          {ai && <>
            <div className="form-grid">
              <label>Provider
                <select value={chosenProvider.value} disabled={aiBusy}
                  onChange={(e) => {
                    const next = ai.providers.find((p) => p.value === e.target.value);
                    if (!next) return;
                    setAiBase(next.base_url);
                    setAiModel(next.model);
                    setAiKey("");
                    setAiConnection("");
                    void saveAi({
                      baseUrl: next.base_url,
                      model: next.model,
                      apiKey: "",
                      enabled: false,
                    });
                  }}>
                  {ai.providers.map((p) => (
                    <option key={p.value} value={p.value}>{p.label}</option>
                  ))}
                </select>
                <small>{chosenProvider.note}</small>
              </label>
              <label>{chosenProvider.value === "minimax"
                ? "MiniMax subscription key" : "API key"}
                <input type="password" value={aiKey}
                  disabled={ai.is_local}
                  placeholder={ai.is_local ? "Not needed for a local model"
                    : ai.api_key_set ? "Protected for your Windows account"
                      : "Paste your key"}
                  onChange={(e) => setAiKey(e.target.value)} />
                <small>{ai.is_local
                  ? "Local models do not need a cloud key."
                  : "Encrypted with Windows user protection. It is only decrypted when Northstar calls the provider above."}</small>
              </label>
              <label>Months of history to share
                <input type="number" min="1" max="12" value={ai.months}
                  onChange={(e) => void saveAi({ months: Number(e.target.value) })} />
              </label>
            </div>
            <details className="formula-details ai-advanced">
              <summary>Advanced connection settings</summary>
              <div className="form-grid">
                <label>API address
                  <input value={aiBase} placeholder="http://localhost:11434/v1"
                    onChange={(e) => setAiBase(e.target.value)} />
                  <small>Cloud addresses must use HTTPS. Local models must use
                    localhost or a loopback address.</small>
                </label>
                <label>Model
                  <input value={aiModel} placeholder="MiniMax-M3"
                    onChange={(e) => setAiModel(e.target.value)} />
                </label>
              </div>
            </details>
            <div className="button-row">
              <button type="button" className="primary-button"
                disabled={aiBusy || (!ai.is_local && !ai.api_key_set && !aiKey.trim())}
                onClick={() => void saveAndTestAi()}>
                {aiBusy ? "Checking…" : "Save and test connection"}
              </button>
            </div>
            {aiConnection && <p className="success-text">{aiConnection}</p>}
            <strong className="ai-scope-title">What may be sent</strong>
            <div className="settings-list">
              {ai.scopes.map((scope) => (
                <label className="setting-row ai-scope" key={scope.value}>
                  <input type="radio" name="ai-scope" value={scope.value}
                    checked={ai.scope === scope.value} disabled={aiBusy}
                    onChange={() => void saveAi({ scope: scope.value })} />
                  <span><strong>{scope.label}</strong><small>{scope.note}</small></span>
                </label>
              ))}
            </div>
            <div className="button-row">
              <button type="button" className="ghost-button" disabled={aiBusy}
                onClick={() => void showPayload()}>
                Show exactly what would be sent
              </button>
              {ai.api_key_set && <button type="button" className="ghost-button"
                disabled={aiBusy} onClick={() => void dropKey()}>
                Forget my key and switch off
              </button>}
            </div>
            {aiPreview && (
              <details className="formula-details" open>
                <summary>
                  {aiPreview.transaction_count > 0
                    ? `${aiPreview.transaction_count} transactions, ${(aiPreview.bytes / 1024).toFixed(1)} KB`
                    : `${(aiPreview.bytes / 1024).toFixed(1)} KB, no individual transactions`}
                </summary>
                <pre className="ai-payload">{aiPreview.json}</pre>
              </details>
            )}
            {aiError && <div className="inline-error">{aiError}</div>}
          </>}
          </div>
          </div>
        </article>
      </div>

      <h3 className="insight-section">Everyday preferences</h3>
      <div className="settings-grid">
        <article className="form-card">
          <h3>Everyday view</h3>
          <div className="form-grid">
            <label>Open Northstar on<select value={landingPage} onChange={(e)=>changeLanding(e.target.value as LandingPage)}><option value="home">Home</option><option value="plan">Plan</option><option value="insights">Insights</option><option value="transactions">Transactions</option></select></label>
            <label>Financial period<select value={defaultPeriod} onChange={(e)=>changePeriod(Number(e.target.value) as AnalysisPeriod)}><option value={30}>Last 30 days</option><option value={90}>Last 90 days</option></select></label>
            <label>Display density<select value={density} onChange={(e)=>changeDensity(e.target.value as Density)}><option value="comfortable">Comfortable</option><option value="compact">Compact</option></select></label>
            <label>Weeks start on<select value={weekStart} onChange={(e)=>changeWeekStart(e.target.value as WeekStart)}><option value="monday">Monday</option><option value="sunday">Sunday</option></select><small>Sets the columns in the spending calendar.</small></label>
            <label>Preferred savings target<select value={savingsPreference.style} onChange={(e)=>changeSavings(e.target.value as SavingsStyle,savingsPreference.value)}><option value="percentage">Percentage of income</option><option value="amount">Monthly amount</option></select></label>
            <label>{savingsPreference.style==="percentage"?"Savings percentage":"Savings amount"}<input type="number" min="0" step="1" value={savingsPreference.value} onChange={(e)=>changeSavings(savingsPreference.style,Number(e.target.value))}/><small>{savingsPreference.style==="percentage"?"Used as the starting suggestion for a new Plan.":"Used as the starting monthly savings suggestion."}</small></label>
          </div>
          <label className="check-label"><input type="checkbox" checked={showSecondary} onChange={(e)=>changeSecondary(e.target.checked)}/> Show extra Home sections such as monthly cash flow, merchants, and goals</label>
          <label className="check-label"><input type="checkbox" checked={showNetWorth} onChange={(e)=>changeNetWorth(e.target.checked)}/> Show net worth in Insights</label>
          <p className="guidance">These choices stay on this computer and survive a relaunch.</p>
        </article>
        <article className="form-card">
          <h3>How categorization works</h3>
          <p className="guidance">Northstar protects payments and internal transfers first, then uses your saved merchant rules, the bank statement memo, known merchant mappings, and finally Uncategorized.</p>
          <p>Only high-impact or genuinely uncertain rows are promoted for review. Ordinary recognized activity updates charts automatically.</p>
        </article>
      </div>

      <h3 className="insight-section" id="merchant-rules">Merchant and category rules</h3>
      <article className="chart-card">
        <p className="guidance">Rules you save from Transactions are editable here. Disabling a rule preserves it for later; deleting it does not rewrite historical transactions.</p>
        {categorySettings?.rules.length ? categorySettings.rules.map(rule=><div className="rule-row" key={rule.id}><span><strong>{rule.merchant_normalized}</strong><small>{rule.hit_count||0} future import match{rule.hit_count===1?"":"es"}</small></span><select value={rule.category} disabled={busy===`rule-${rule.id}`} onChange={(e)=>void editRule(rule.id,e.target.value,!!rule.enabled)}>{categorySettings.categories.map(category=><option key={category}>{category}</option>)}</select><label className="inline-check"><input type="checkbox" checked={!!rule.enabled} onChange={(e)=>void editRule(rule.id,rule.category,e.target.checked)}/> Enabled</label><button className="ghost-button" disabled={busy===`rule-${rule.id}`} onClick={()=>void removeRule(rule.id)}>Delete</button></div>) : <p className="guidance">No saved merchant rules yet. Save one while correcting a transaction.</p>}
      </article>

      <h3 className="insight-section" id="income-sources">Stable income sources</h3>
      <details className="chart-card settings-collapse"
        open={focusAnchor === "income-sources"}>
        <summary>
          {categorySettings?.income_sources.length
            ? `${categorySettings.income_sources.length} source${categorySettings.income_sources.length === 1 ? "" : "s"} · ${categorySettings.income_sources.filter(s => s.status === "confirmed" || s.status === "excluded").length} reviewed`
            : "No income sources yet"}
        </summary>
        <p className="guidance">Northstar uses recurring payroll as the monthly planning baseline. Confirm a recognized source or exclude one that should not count as stable pay. A source stays unreviewed until you choose — it will not silently count as stable pay.</p>
        {categorySettings?.income_sources.length?categorySettings.income_sources.map(source=>{const reviewed=source.status==="confirmed"||source.status==="excluded";return <div className="rule-row" key={source.source_normalized}><span><strong>{source.source}</strong><small>{source.tx_count} deposit{source.tx_count===1?"":"s"} across {source.months_seen} month{source.months_seen===1?"":"s"}</small><small className={source.status==="confirmed"?"status-tag status-ok":source.status==="excluded"?"status-tag status-muted":"status-tag status-warn"}>{source.status==="confirmed"?"Using as stable income":source.status==="excluded"?"Excluded from stable income":"Not reviewed yet — choose one"}</small></span><select value={reviewed?source.status:""} disabled={busy===`income-${source.source_normalized}`} onChange={(e)=>void changeIncomeSource(source.source_normalized,e.target.value as "confirmed"|"excluded")}>{!reviewed&&<option value="" disabled>Choose…</option>}<option value="confirmed">Use as stable income</option><option value="excluded">Exclude from stable income</option></select>{saved===`income-${source.source_normalized}`&&<span className="success-text saved-flag">Saved</span>}</div>;}):<p className="guidance">Recurring payroll sources appear after income is imported.</p>}
      </details>

      <h3 className="insight-section" id="recurring-costs">Recurring merchants</h3>
      <details className="chart-card settings-collapse"
        open={focusAnchor === "recurring-costs"}>
        <summary>
          {categorySettings?.recurring.length
            ? `${categorySettings.recurring.length} recurring merchant${categorySettings.recurring.length === 1 ? "" : "s"} detected`
            : "None detected yet"}
        </summary>
        <p className="guidance">Northstar detects repeated outflows using merchant, cadence, amount stability, and whether the category is a known bill. Say so when it gets one wrong.</p>
        {/* Two choices, matching Insights. A cost either recurs or it does
            not; "Automatic" described how Northstar found it, which is
            provenance, so it is a badge rather than a third thing to decide.
            Detection itself is unchanged underneath. */}
        {categorySettings?.recurring.length ? categorySettings.recurring.map(item=>{const edit=recurringEdits[item.merchant_normalized]??{name:item.merchant,category:item.category};const stored=recurringStatus(item.merchant_normalized,item.recurring_status==="confirmed"?"recurring":"automatic");const suggested=stored==="automatic";const status=suggested?"recurring":stored;return <div className="recurring-edit-row" key={item.merchant_normalized}><div><strong>{item.merchant}</strong><small>{item.cadence} · about {item.avg_amount.toLocaleString(undefined,{style:"currency",currency:"CAD"})}</small>{suggested&&<span className="suggested-badge">Suggested</span>}</div><label>Display name<input value={edit.name} onChange={(e)=>setRecurringEdits({...recurringEdits,[item.merchant_normalized]:{...edit,name:e.target.value}})}/></label><label>Category<select value={edit.category} onChange={(e)=>setRecurringEdits({...recurringEdits,[item.merchant_normalized]:{...edit,category:e.target.value}})}>{categorySettings.categories.map(category=><option key={category}>{category}</option>)}</select></label><label>Status<select value={status} disabled={busy===`recurring-${item.merchant_normalized}`} onChange={(e)=>void changeRecurring(item.merchant_normalized,e.target.value)}><option value="recurring">Recurring</option><option value="not_recurring">Not recurring</option></select></label><button className="ghost-button" disabled={busy===`recurring-${item.merchant_normalized}`} onClick={()=>void changeRecurring(item.merchant_normalized,status,true)}>Save details</button>{saved===`recurring-${item.merchant_normalized}`&&<span className="success-text saved-flag">Saved</span>}</div>}) : <p className="guidance">No recurring expenses have enough history yet.</p>}
      </details>

      <h3 className="insight-section">Data safety</h3>
      <div className="settings-grid">
        <article className="form-card">
          <h3>Repair older imports</h3>
          {safety?.repair.needed ? (
            <>
              <p className="warning-text">
                {safety.repair.affected_rows} transaction{safety.repair.affected_rows === 1 ? "" : "s"} in {safety.repair.batch_count} older import{safety.repair.batch_count === 1 ? "" : "s"} need signed-amount and classification repair.
              </p>
              {safety.repair.batches.map((batch) => (
                <div className="rank-row" key={batch.batch_id}>
                  <span>{batch.filename}<small>{batch.account_name} · imported {batch.imported_at}</small></span>
                  <strong>{batch.affected_rows} rows</strong>
                </div>
              ))}
              <p className="guidance">
                Choose the original files above. Northstar matches their saved
                hashes and every row before enabling repair. The files are read
                in place and are never copied into the app.
              </p>
              <button disabled={!!busy} onClick={() => void chooseRepairSources()}>
                {busy === "repair-preview" ? "Checking files…" : "Choose original files…"}
              </button>
              {repairPreview && (
                <div className={repairPreview.ready ? "repair-ready" : "inline-error"}>
                  <strong>
                    {repairPreview.matched_rows} of {repairPreview.affected_rows} rows matched
                  </strong>
                  <p>
                    {repairPreview.ready
                      ? "Ready. A validated backup will be created before one transaction changes."
                      : `${repairPreview.unmatched_rows} rows are still unmatched. Choose every listed original file.`}
                  </p>
                  {repairPreview.ready && (
                    <button disabled={!!busy} onClick={() => void repair()}>
                      {busy === "repair" ? "Repairing safely…" : "Back up and repair data"}
                    </button>
                  )}
                </div>
              )}
            </>
          ) : (
            <p className="success-text">No legacy imported batches need repair.</p>
          )}
          {repairResult && (
            <div className="repair-complete">
              <strong>{repairResult.message}</strong>
              <p>
                Review items: {repairResult.review_before} → {repairResult.review_after}.
                Backup: {repairResult.backup_name}
              </p>
              <button className="ghost-button" disabled={!!busy} onClick={() => void restore(repairResult.backup_path)}>
                Restore pre-repair backup
              </button>
            </div>
          )}
        </article>

        {/* A local-first app that will not hand the data back is only a
            different kind of lock-in. Every row, in a shape any spreadsheet
            or other finance app can open. */}
        <article className="form-card">
          <h3>Export your transactions</h3>
          <p className="guidance">
            Every transaction Northstar holds, written to a CSV file you
            choose. Nothing is uploaded and nothing is deleted here; this is a
            copy, for a spreadsheet, your records, or another app.
          </p>
          {exportNote && <p className="success-text">{exportNote}</p>}
          {exportError && <div className="inline-error">{exportError}</div>}
          <div className="button-row">
            <button type="button" className="ghost-button" disabled={exporting}
              onClick={() => void runExport()}>
              {exporting ? "Writing…" : "Export to CSV"}
            </button>
          </div>
        </article>
        <article className="form-card danger-card settings-wide">
          <h3>Reset all financial data</h3>
          <p>
            Removes transactions, imports, accounts, plans, goals, balances,
            rules, and profiles. Northstar creates a validated backup first.
          </p>
          <p className="guidance">
            App preferences such as the selected chart period are preserved
            unless you choose a complete reset.
          </p>
          <button className="danger-button" disabled={!!busy || !(safety?.reset.transaction_count || safety?.reset.net_worth_reading_count)} onClick={() => setShowReset(true)}>
            Reset all financial data…
          </button>
          {resetResult && (
            <div className="repair-complete">
              <strong>{resetResult.message}</strong>
              <p>Backup: {resetResult.backup_name}</p>
              <button className="ghost-button" disabled={!!busy} onClick={() => void restore(resetResult.backup_path)}>
                Restore data from this backup
              </button>
            </div>
          )}
        </article>
      </div>

      <details className="chart-card settings-collapse">
        <summary>
          Recent backups{data?.backups.length ? ` · ${data.backups.length}` : ""}
        </summary>
        {data?.backups.length ? (
          data.backups.slice(0, 8).map((item) => (
            <div className="rank-row" key={item.path}>
              <span>{item.name}<small>{new Date(item.created_at).toLocaleString()}</small></span>
              <strong>{Math.max(1, Math.round(item.size_bytes / 1024))} KB</strong>
            </div>
          ))
        ) : <p className="guidance">No local backups yet.</p>}
      </details>

      {pending && (
        <div className="confirm-panel" role="dialog" aria-modal="true">
          <strong>Restore this backup?</strong>
          <p>{fileName(pending)}</p>
          <p>The current database will be backed up first, then replaced with the selected validated copy.</p>
          <div className="button-row">
            <button disabled={!!busy} onClick={() => void restore()}>
              {busy === "restore" ? "Restoring…" : "Restore backup"}
            </button>
            <button className="ghost-button" onClick={() => setPending("")}>Cancel</button>
          </div>
        </div>
      )}

      {showReset && (
        <div className="confirm-panel reset-confirm" role="dialog" aria-modal="true">
          <strong>Reset all Northstar financial data?</strong>
          <p>
            This removes {safety?.reset.transaction_count ?? 0} transactions
            and {safety?.reset.account_count ?? 0} accounts from the active database.
            A restorable backup is created first.
          </p>
          {(safety?.reset.net_worth_reading_count ?? 0) > 0 && (
            <p>
              It also removes {safety?.reset.net_worth_reading_count} monthly
              net worth reading{safety?.reset.net_worth_reading_count === 1 ? "" : "s"}.
              Those were typed by hand, so re-importing statements will not
              bring them back.
            </p>
          )}
          <label>
            Type RESET to continue
            <input value={resetText} onChange={(event) => setResetText(event.target.value)} autoFocus />
          </label>
          <label className="check-label">
            <input type="checkbox" checked={completeReset} onChange={(event) => setCompleteReset(event.target.checked)} />
            Also clear app preferences
          </label>
          <div className="button-row">
            <button className="danger-button" disabled={busy === "reset" || resetText !== "RESET"} onClick={() => void reset()}>
              {busy === "reset" ? "Backing up and resetting…" : "Reset financial data"}
            </button>
            <button className="ghost-button" onClick={() => { setShowReset(false); setResetText(""); }}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

export default SettingsView;
