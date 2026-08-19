import { getVersion } from "@tauri-apps/api/app";
import { money } from "./money";
import { UpdatePanel } from "./UpdatePanel";
import { useCallback, useEffect, useMemo, useState } from "react";

const AI_CONSENT = `Turning this on sends your financial figures to an outside company.

SignalSpace is offline until you do this. After it, the data you selected leaves this computer every time you ask a question. The provider's terms govern what happens to it: they may log it, keep it, or train on it. SignalSpace cannot control that and is not responsible for it.

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
  clearSignalSpacePreferences, readAnalysisPeriod, readDensity,
  readHomeSecondary, readLandingPage, readShowNetWorth, readSavingsPreference,
  readWeekStart,
  saveAnalysisPeriod, saveDensity, saveHomeSecondary, saveLandingPage,
  saveShowNetWorth, saveSavingsPreference, saveWeekStart,
  type AnalysisPeriod, type Density, type LandingPage, type SavingsStyle,
  type WeekStart,
} from "./preferences";
import { ProfilesPanel } from "./ProfilesPanel";
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
  onProfileChanged: () => void;
  focusAnchor?: string;
  focusToken?: number;
  /** Re-opens the getting-started card on Home for the open profile. */
  onShowGuide?: () => void;
}

function fileName(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}

function SettingsView({
  onDataChanged, onProfileChanged, focusAnchor, focusToken, onShowGuide,
}: Props) {
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
      if (completeReset) clearSignalSpacePreferences();
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
          <h2>How SignalSpace works for you</h2>
          <p>
            Everything here is stored on this computer, and SignalSpace never
            requires an account.
          </p>
        </div>
      </div>

      {error && <div className="inline-error" role="alert">{error}</div>}

      {/* First, because which finances are open decides what every other
          screen means. */}
      <h3 className="insight-section" id="profiles">Profiles</h3>
      <ProfilesPanel onSwitched={onProfileChanged} />

      <h3 className="insight-section">Balances and accounts</h3>
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
            No balances recorded yet. SignalSpace never infers one from your
            transactions, so Safe to Spend stays unavailable until you add one.
          </p>
        )}
        <h4>Add or update a balance</h4>
        <div className="form-grid">
          <label>SignalSpace account
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

      {!ai ? (
        // A panel that only ever says "Loading…" is how this shipped
        // broken: the settings request was rejected and the reason was
        // never shown. Say what went wrong and offer another go.
        aiError ? (
          <article className="form-card">
            <div className="inline-error">
              AI assistance could not be loaded. {aiError}
            </div>
            <div className="button-row">
              <button type="button" className="ghost-button"
                onClick={() => void loadAi()}>Try again</button>
            </div>
          </article>
        ) : <p className="guidance">Loading…</p>
      ) : <>
        {/* The state used to be a checkbox two paragraphs down inside a card
            of prose, and the prose said the same thing about privacy three
            times over. One strip, saying what is true right now. */}
        {(() => {
          const ready = ai.is_local || ai.api_key_set || aiKey.trim();
          const state = !ai.enabled ? "off" : ai.is_local ? "local" : "on";
          return (
            <article className={`form-card ai-status ai-status-${state}`}>
              <label className={`toggle-row${ready ? "" : " toggle-row-disabled"}`}>
                <span className="toggle-copy">
                  <strong>{ai.enabled
                    ? ai.is_local ? "On, and answering on this computer"
                      : "On. Your figures are sent when you ask a question"
                    : "Off. Nothing leaves this computer"}</strong>
                  <small>{ready
                    ? ai.enabled
                      ? ai.is_local
                        ? "The model runs at the address below, so nothing is sent anywhere."
                        : `Asking a question sends the data selected under What gets sent to ${(ai.base_url || "").replace(/^https?:\/\//, "").split("/")[0]}.`
                      : "Switch this on to have findings explained and questions answered."
                    : "Choose a provider and add a key below first."}</small>
                </span>
                <span className="toggle-control">
                  <input type="checkbox" checked={ai.enabled}
                    aria-label="Enable AI assistance"
                    className="toggle-input"
                    disabled={aiBusy || !ready}
                    onChange={(e) => {
                      // Switching this on is the one decision in SignalSpace
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
              <p className="ai-status-note">
                Every figure on every screen is worked out on this computer
                whether this is on or off. AI only explains them, and can
                never change a financial record.
              </p>
              {ai.key_error && <div className="inline-error">{ai.key_error}</div>}
              {ai.model_migrated_from && (
                <p className="success-text">
                  SignalSpace updated the retired MiniMax model
                  {" "}{ai.model_migrated_from} to MiniMax-M3.
                </p>
              )}
            </article>
          );
        })()}

        <div className="settings-grid">
          <article className="form-card ai-card">
            <h3>Where the answers come from</h3>
            <p className="guidance">
              {ai.is_local
                // Repeating the "your data leaves" warning while pointed at
                // localhost would train people to ignore it.
                ? "This address is on your own computer, so your figures never leave it and no key is needed."
                : "A cloud provider is an outside company. What it does with what you send is governed by its terms, not SignalSpace's: it may log it, keep it, or train on it. You supply the key and pay for the usage. Pointing this at a model running on this computer gives you the same feature with nothing leaving."}
            </p>
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
                  : "Encrypted with Windows user protection. It is only decrypted when SignalSpace calls the provider above."}</small>
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
              {ai.api_key_set && <button type="button" className="ghost-button"
                disabled={aiBusy} onClick={() => void dropKey()}>
                Forget my key and switch off
              </button>}
            </div>
            {aiConnection && <p className="success-text">{aiConnection}</p>}
          </article>

          {/* The amount of someone's financial life leaving the machine is
              the whole decision, so it gets a card rather than a heading
              halfway down a column. */}
          <article className="form-card ai-card">
            <h3>What gets sent</h3>
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
            <div className="form-grid">
              <label>Months of history to share
                <input type="number" min="1" max="12" value={ai.months}
                  onChange={(e) => void saveAi({ months: Number(e.target.value) })} />
              </label>
            </div>
            <div className="button-row">
              <button type="button" className="ghost-button" disabled={aiBusy}
                onClick={() => void showPayload()}>
                Show exactly what would be sent
              </button>
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
          </article>
        </div>
      </>}

      <h3 className="insight-section">Everyday preferences</h3>
      <div className="settings-grid">
        <article className="form-card settings-wide">
          <h3>Everyday view</h3>
          <div className="form-grid">
            <label>Open SignalSpace on<select value={landingPage} onChange={(e)=>changeLanding(e.target.value as LandingPage)}><option value="home">Home</option><option value="plan">Plan</option><option value="insights">Insights</option><option value="transactions">Transactions</option></select></label>
            <label>Financial period<select value={defaultPeriod} onChange={(e)=>changePeriod(Number(e.target.value) as AnalysisPeriod)}><option value={30}>Last 30 days</option><option value={90}>Last 90 days</option></select></label>
            <label>Display density<select value={density} onChange={(e)=>changeDensity(e.target.value as Density)}><option value="comfortable">Comfortable</option><option value="compact">Compact</option></select></label>
            <label>Weeks start on<select value={weekStart} onChange={(e)=>changeWeekStart(e.target.value as WeekStart)}><option value="monday">Monday</option><option value="sunday">Sunday</option></select><small>Sets the columns in the spending calendar.</small></label>
            <label>Preferred savings target<select value={savingsPreference.style} onChange={(e)=>changeSavings(e.target.value as SavingsStyle,savingsPreference.value)}><option value="percentage">Percentage of income</option><option value="amount">Monthly amount</option></select></label>
            <label>{savingsPreference.style==="percentage"?"Savings percentage":"Savings amount"}<input type="number" min="0" step="1" value={savingsPreference.value} onChange={(e)=>changeSavings(savingsPreference.style,Number(e.target.value))}/><small>{savingsPreference.style==="percentage"?"Used as the starting suggestion for a new Plan.":"Used as the starting monthly savings suggestion."}</small></label>
          </div>
          <label className="check-label"><input type="checkbox" checked={showSecondary} onChange={(e)=>changeSecondary(e.target.checked)}/> Show extra Home sections such as monthly cash flow, merchants, and goals</label>
          <label className="check-label"><input type="checkbox" checked={showNetWorth} onChange={(e)=>changeNetWorth(e.target.checked)}/> Show net worth in Insights</label>
          <p className="guidance">These choices stay on this computer and survive a relaunch.</p>
          {onShowGuide && <div className="button-row">
            <button type="button" className="ghost-button"
              onClick={() => onShowGuide()}>
              Show the getting started guide
            </button>
          </div>}
          {onShowGuide && <p className="guidance">
            Re-opens the first-run checklist on Home for the profile you
            have open. It normally appears only while a profile has no
            transactions.
          </p>}
        </article>
      </div>

      {/* "How categorization works" had its own card here and nothing you
          could do about any of it. The order it describes only matters while
          you are looking at a rule, so the sentence lives with the rules and
          the card is gone. */}
      <h3 className="insight-section">What SignalSpace has learned</h3>
      <article className="chart-card" id="merchant-rules">
        <h3>Merchant and category rules</h3>
        <p className="guidance">Payments and internal transfers are protected first, then your saved rules below, then the statement memo, then known merchants, and anything left is Uncategorized.</p>
        <p className="guidance">Rules you save from Transactions are editable here. Disabling a rule preserves it for later; deleting it does not rewrite historical transactions.</p>
        {categorySettings?.rules.length ? categorySettings.rules.map(rule=><div className="rule-row" key={rule.id}><span><strong>{rule.merchant_normalized}</strong><small>{rule.hit_count||0} future import match{rule.hit_count===1?"":"es"}</small></span><select value={rule.category} disabled={busy===`rule-${rule.id}`} onChange={(e)=>void editRule(rule.id,e.target.value,!!rule.enabled)}>{categorySettings.categories.map(category=><option key={category}>{category}</option>)}</select><label className="inline-check"><input type="checkbox" checked={!!rule.enabled} onChange={(e)=>void editRule(rule.id,rule.category,e.target.checked)}/> Enabled</label><button className="ghost-button" disabled={busy===`rule-${rule.id}`} onClick={()=>void removeRule(rule.id)}>Delete</button></div>) : <p className="guidance">No saved merchant rules yet. Save one while correcting a transaction.</p>}
      </article>

      <details className="chart-card settings-collapse" id="income-sources"
        open={focusAnchor === "income-sources"}>
        <summary>
          Income sources
          <span>{categorySettings?.income_sources.length
            ? `${categorySettings.income_sources.length} source${categorySettings.income_sources.length === 1 ? "" : "s"} · ${categorySettings.income_sources.filter(s => s.status === "confirmed" || s.status === "excluded").length} reviewed`
            : "none yet"}</span>
        </summary>
        <p className="guidance">SignalSpace's historical planning average includes genuine income unless you exclude it. Payroll can appear as an expected payday automatically. Use as income confirms another regular source for the Radar; Exclude removes it from planning.</p>
        {categorySettings?.income_sources.length?categorySettings.income_sources.map(source=>{const reviewed=source.status==="confirmed"||source.status==="excluded";return <div className="rule-row" key={source.source_normalized}><span><strong>{source.source}</strong><small>{source.tx_count} deposit{source.tx_count===1?"":"s"} across {source.months_seen} month{source.months_seen===1?"":"s"}</small><small className={source.status==="confirmed"?"status-tag status-ok":source.status==="excluded"?"status-tag status-muted":"status-tag status-warn"}>{source.status==="confirmed"?"Using as income":source.status==="excluded"?"Excluded from planning income":"Not reviewed yet — choose one"}</small></span><select value={reviewed?source.status:""} disabled={busy===`income-${source.source_normalized}`} onChange={(e)=>void changeIncomeSource(source.source_normalized,e.target.value as "confirmed"|"excluded")}>{!reviewed&&<option value="" disabled>Choose…</option>}<option value="confirmed">Use as income</option><option value="excluded">Exclude</option></select>{saved===`income-${source.source_normalized}`&&<span className="success-text saved-flag">Saved</span>}</div>;}):<p className="guidance">Income sources appear after income is imported.</p>}
      </details>

      <details className="chart-card settings-collapse" id="recurring-costs"
        open={focusAnchor === "recurring-costs"}>
        <summary>
          Recurring merchants
          <span>{categorySettings?.recurring.length
            ? `${categorySettings.recurring.length} detected`
            : "none detected yet"}</span>
        </summary>
        <p className="guidance">SignalSpace detects repeated outflows using merchant, cadence, amount stability, and whether the category is a known bill. Say so when it gets one wrong.</p>
        {/* Two choices, matching Insights. A cost either recurs or it does
            not; "Automatic" described how SignalSpace found it, which is
            provenance, so it is a badge rather than a third thing to decide.
            Detection itself is unchanged underneath. */}
        {categorySettings?.recurring.length ? categorySettings.recurring.map(item=>{const edit=recurringEdits[item.merchant_normalized]??{name:item.merchant,category:item.category};const stored=recurringStatus(item.merchant_normalized,item.recurring_status==="confirmed"?"recurring":"automatic");const suggested=stored==="automatic";const status=suggested?"recurring":stored;return <div className="recurring-edit-row" key={item.merchant_normalized}><div><strong>{item.merchant}</strong><small>{item.cadence} · about {item.avg_amount.toLocaleString(undefined,{style:"currency",currency:"CAD"})}</small>{suggested&&<span className="suggested-badge">Suggested</span>}</div><label>Display name<input value={edit.name} onChange={(e)=>setRecurringEdits({...recurringEdits,[item.merchant_normalized]:{...edit,name:e.target.value}})}/></label><label>Category<select value={edit.category} onChange={(e)=>setRecurringEdits({...recurringEdits,[item.merchant_normalized]:{...edit,category:e.target.value}})}>{categorySettings.categories.map(category=><option key={category}>{category}</option>)}</select></label><label>Status<select value={status} disabled={busy===`recurring-${item.merchant_normalized}`} onChange={(e)=>void changeRecurring(item.merchant_normalized,e.target.value)}><option value="recurring">Recurring</option><option value="not_recurring">Not recurring</option></select></label><button className="ghost-button" disabled={busy===`recurring-${item.merchant_normalized}`} onClick={()=>void changeRecurring(item.merchant_normalized,status,true)}>Save details</button>{saved===`recurring-${item.merchant_normalized}`&&<span className="success-text saved-flag">Saved</span>}</div>}) : <p className="guidance">No recurring expenses have enough history yet.</p>}
      </details>

      {/* Everything that copies, hands back or destroys the data now lives
          together and near the bottom. Backups were in a grid at the top of
          the page, the CSV export and the reset were under "Data safety" much
          further down, and the list of backups came after both, so the one
          question "how do I get my data back" was answered in three places. */}
      <h3 className="insight-section" id="local-data">Local data and recovery</h3>
      <div className="settings-grid">
        <article className="form-card">
          <h3>Backups</h3>
          <p className="guidance">
            A validated copy of the active profile&apos;s database, kept on this
            computer. Worth making before a large import or any change you are
            unsure about.
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
          <details className="formula-details">
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
        </article>

        {/* A local-first app that will not hand the data back is only a
            different kind of lock-in. Every row, in a shape any spreadsheet
            or other finance app can open. */}
        <article className="form-card">
          <h3>Export your transactions</h3>
          <p className="guidance">
            Every transaction in the active profile, written to a CSV file you
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

        <article className="form-card settings-wide">
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
                Choose the original files above. SignalSpace matches their saved
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

        <article className="form-card danger-card settings-wide">
          <h3>Reset all financial data</h3>
          <p>
            Removes transactions, imports, accounts, plans, goals, balances,
            rules, and profiles. SignalSpace creates a validated backup first.
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

      <h3 className="insight-section">About and updates</h3>
      <div className="settings-grid">
        <article className="form-card">
          <h3>About</h3>
          <dl>
            <div><dt>Product</dt><dd>SignalSpace {version || "…"}</dd></div>
            <div><dt>Storage</dt><dd>{data?.data_directory ?? "Loading…"}</dd></div>
            <div><dt>Database</dt><dd>{data?.database_name ?? "finance.db"}</dd></div>
            <div><dt>Finance data</dt><dd>Stays on this computer. Every calculation runs in the local sidecar.</dd></div>
            <div><dt>Network</dt><dd>Nothing is sent unless you ask. AI assistance contacts the provider you configure; checking for updates contacts GitHub. Both are off until you act.</dd></div>
          </dl>
        </article>
        <UpdatePanel version={version} />
      </div>

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
          <strong>Reset all SignalSpace financial data?</strong>
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
