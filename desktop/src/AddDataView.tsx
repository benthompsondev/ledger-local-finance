import { useCallback, useRef, useState } from "react";
import {
  applyCsvMapping,
  confirmImport,
  createAccount,
  pickImportFiles,
  previewImport,
  resetCsvProfile,
} from "./api";
import { signedMoneyCents as moneyCents } from "./money";
import ManualTransactionForm from "./ManualTransactionForm";
import type {
  Account,
  AccountType,
  CsvMapping,
  ImportFileResult,
  ImportOutcome,
  PreviewFile,
} from "./types";

interface Props {
  onGoHome: () => void;
  onGoTransactions: () => void;
  onDataChanged: () => void;
}

/** Preselect the sensible destination account for a previewed file:
 * the saved CSV-profile suggestion first, then the first active account
 * whose type matches the detected statement type, then nothing (the user
 * must choose — imports never guess silently). */
function suggestedAccountId(
  file: PreviewFile,
  accounts: Account[],
): number | null {
  if (
    file.suggested_account_ref &&
    accounts.some((a) => a.id === file.suggested_account_ref)
  ) {
    return file.suggested_account_ref;
  }
  if (file.suggested_account_type) {
    const match = accounts.find((a) => a.type === file.suggested_account_type);
    if (match) return match.id;
  }
  if (accounts.length === 1) return accounts[0].id;
  return null;
}

function AddDataView({ onGoHome, onGoTransactions, onDataChanged }: Props) {
  const [files, setFiles] = useState<PreviewFile[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [accountTypes, setAccountTypes] = useState<AccountType[]>([]);
  const [chosen, setChosen] = useState<Record<string, number | null>>({});
  // Date conventions the user resolved for a file whose dates could be read
  // two ways. Held in a ref as well so the preview refresh, which runs from a
  // stable callback, always sends the latest answer.
  const [dateFormats, setDateFormats] = useState<Record<string, string>>({});
  const dateFormatsRef = useRef<Record<string, string>>({});
  const [banks, setBanks] = useState<Record<string, string>>({});
  const banksRef = useRef<Record<string, string>>({});
  const [importModes, setImportModes] = useState<
    Record<string, "new" | "reexport">
  >({});
  const importModesRef = useRef<Record<string, "new" | "reexport">>({});
  const [busy, setBusy] = useState<
    "" | "picking" | "previewing" | "creating-account" | "importing"
  >("");
  const [progress, setProgress] = useState<{
    done: number;
    total: number;
    name: string;
  } | null>(null);
  const [attempted, setAttempted] = useState(false);
  const [error, setError] = useState("");
  const [outcome, setOutcome] = useState<ImportOutcome | null>(null);
  const [showNewAccount, setShowNewAccount] = useState(false);
  const [newName, setNewName] = useState("");
  const [newType, setNewType] = useState("chequing");
  const [newInstitution, setNewInstitution] = useState("");
  const [newOpening, setNewOpening] = useState("0");
  const [mappingDrafts, setMappingDrafts] = useState<Record<string, CsvMapping>>({});
  const [mappingOpen, setMappingOpen] = useState<Record<string, boolean>>({});
  const [rememberMapping, setRememberMapping] = useState<Record<string, boolean>>({});

  const choose = useCallback(async () => {
    setError("");
    setOutcome(null);
    setBusy("picking");
    try {
      const paths = await pickImportFiles();
      if (!paths.length) return;
      setBusy("previewing");
      const preview = await previewImport(paths);
      importModesRef.current = {};
      setImportModes({});
      setAccounts(preview.accounts);
      setAccountTypes(preview.account_types);
      const next: Record<string, number | null> = {};
      for (const file of preview.files) {
        next[file.path] = suggestedAccountId(file, preview.accounts);
      }
      setChosen(next);
      if (Object.values(next).some((value) => value !== null)) {
        const checked = await previewImport(paths, preview.files.map((file) => ({
          path: file.path,
          accountId: next[file.path] ?? null,
          mapping: file.mapping,
          importMode: null,
        })));
        setFiles(checked.files);
      } else {
        setFiles(preview.files);
      }
      setMappingDrafts(Object.fromEntries(preview.files
        .filter((file) => file.mapping_info)
        .map((file) => [file.path, file.mapping_info!.suggested])));
      setMappingOpen(Object.fromEntries(preview.files.map((file) => [file.path, file.needs_mapping])));
      setRememberMapping(Object.fromEntries(preview.files.map((file) => [file.path, true])));
      if (!preview.accounts.length) setShowNewAccount(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  }, []);

  const refreshForAccounts = useCallback(async (
    nextChosen: Record<string, number | null>, currentFiles: PreviewFile[],
  ) => {
    setBusy("previewing");
    setError("");
    try {
      const preview = await previewImport(
        currentFiles.map((file) => file.path),
        currentFiles.map((file) => ({
          path: file.path,
          accountId: nextChosen[file.path] ?? null,
          mapping: file.mapping,
          dateFormat: dateFormatsRef.current[file.path] ?? null,
          bank: banksRef.current[file.path] ?? null,
          importMode: importModesRef.current[file.path] ?? null,
        })),
      );
      setFiles(preview.files);
      setAccounts(preview.accounts);
      setAccountTypes(preview.account_types);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  }, []);

  const chooseAccount = useCallback((path: string, accountId: number | null) => {
    const next = { ...chosen, [path]: accountId };
    setChosen(next);
    void refreshForAccounts(next, files);
  }, [chosen, files, refreshForAccounts]);

  const chooseDateFormat = useCallback((path: string, format: string) => {
    dateFormatsRef.current = { ...dateFormatsRef.current, [path]: format };
    setDateFormats(dateFormatsRef.current);
    void refreshForAccounts(chosen, files);
  }, [chosen, files, refreshForAccounts]);

  const chooseBank = useCallback((path: string, bank: string) => {
    banksRef.current = { ...banksRef.current, [path]: bank };
    setBanks(banksRef.current);
    void refreshForAccounts(chosen, files);
  }, [chosen, files, refreshForAccounts]);

  const chooseImportMode = useCallback((
    path: string, mode: "new" | "reexport",
  ) => {
    importModesRef.current = { ...importModesRef.current, [path]: mode };
    setImportModes(importModesRef.current);
    void refreshForAccounts(chosen, files);
  }, [chosen, files, refreshForAccounts]);

  const applyMapping = useCallback(async (file: PreviewFile) => {
    const accountId = chosen[file.path];
    const mapping = mappingDrafts[file.path];
    if (!accountId) {
      setError(`Choose where “${file.filename}” came from before mapping it.`);
      return;
    }
    if (!mapping) return;
    setBusy("previewing");
    setError("");
    try {
      const preview = await applyCsvMapping({
        path: file.path,
        accountId,
        mapping,
        saveProfile: rememberMapping[file.path] !== false,
        profileName: file.filename.replace(/\.csv$/i, ""),
      });
      const mapped = preview.files[0];
      setFiles((current) => current.map((item) =>
        item.path === file.path ? mapped : item));
      setMappingOpen((current) => ({ ...current, [file.path]: false }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  }, [chosen, mappingDrafts, rememberMapping]);

  const submitNewAccount = useCallback(async () => {
    setError("");
    setBusy("creating-account");
    try {
      const created = await createAccount({
        name: newName,
        accountType: newType,
        institution: newInstitution,
        openingBalance: Number(newOpening) || 0,
      });
      setAccounts(created.accounts);
      setAccountTypes(created.account_types);
      const next = { ...chosen };
      for (const file of files) {
        if (!next[file.path]) next[file.path] = created.created_id;
      }
      setChosen(next);
      await refreshForAccounts(next, files);
      setShowNewAccount(false);
      setNewName("");
      setNewInstitution("");
      setNewOpening("0");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  }, [chosen, files, newName, newType, newInstitution, newOpening, refreshForAccounts]);

  const resetProfile = useCallback(async (file: PreviewFile) => {
    setError("");
    setBusy("previewing");
    try {
      await resetCsvProfile(file.path);
      const preview = await previewImport([file.path], [{
        path: file.path,
        accountId: chosen[file.path] ?? null,
        mapping: null,
      }]);
      setFiles((current) => current.map((item) =>
        item.path === file.path ? preview.files[0] : item));
      setAccounts(preview.accounts);
      setAccountTypes(preview.account_types);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally { setBusy(""); }
  }, [chosen]);

  const importable = files.filter((f) =>
    !f.error && !f.dedup_choice_required && f.tx_count > 0);
  const unassigned = importable.filter((f) => !chosen[f.path]);

  // Files import one at a time so the user always sees which file is being
  // processed, a failure in one file never blocks or rolls back the others
  // (each file is already its own atomic batch in the engine), and a slow
  // file shows honest progress instead of a silently spinning button.
  const runImport = useCallback(async () => {
    setError("");
    setAttempted(true);
    if (importable.length === 0) return;
    if (unassigned.length > 0) {
      setError(
        `Choose a destination account for: ${unassigned
          .map((f) => f.filename)
          .join(", ")}. Each highlighted file below has an "Import into" selector.`,
      );
      return;
    }
    setBusy("importing");
    const results: ImportFileResult[] = [];
    const failures: { filename: string; message: string }[] = [];
    const totals = { inserted: 0, skipped: 0, flagged: 0 };
    try {
      for (let i = 0; i < importable.length; i++) {
        const file = importable[i];
        setProgress({
          done: i,
          total: importable.length,
          name: file.filename,
        });
        try {
          const result = await confirmImport([
            {
              path: file.path,
              accountId: chosen[file.path] as number,
              mapping: file.mapping,
              dateFormat: dateFormatsRef.current[file.path] ?? null,
              bank: banksRef.current[file.path] ?? null,
              importMode: importModesRef.current[file.path] ?? null,
            },
          ]);
          results.push(...result.results);
          totals.inserted += result.totals.inserted;
          totals.skipped += result.totals.skipped;
          totals.flagged += result.totals.flagged;
        } catch (cause) {
          failures.push({
            filename: file.filename,
            message: cause instanceof Error ? cause.message : String(cause),
          });
        }
      }
      setOutcome({ results, totals, failures });
      // Keep only the files that failed so the user can retry just those.
      const failedNames = new Set(failures.map((f) => f.filename));
      setFiles((prev) => prev.filter((f) => failedNames.has(f.filename)));
      setAttempted(false);
      if (results.length > 0) onDataChanged();
    } finally {
      setBusy("");
      setProgress(null);
    }
  }, [importable, unassigned, chosen, onDataChanged]);

  const typeLabel = (value: string) =>
    accountTypes.find((t) => t.value === value)?.label ?? value;

  return (
    <section className="workflow-panel">
      <div className="panel-head">
        <div>
          <span className="eyebrow">Add data</span>
          <h2>Import bank statements</h2>
          <p>
            Export a CSV from your bank and choose it here. Your files stay on
            this computer. PDF import is beta and works only for a few known
            statement layouts.
          </p>
        </div>
        <button type="button" onClick={() => void choose()} disabled={busy !== ""}>
          {busy === "picking"
            ? "Choosing…"
            : busy === "previewing"
              ? "Reading files…"
              : "Choose statement files…"}
        </button>
      </div>

      {/* Importing a statement is what almost everyone came here to do.
          Manual entry is for cash purchases and corrections, so it waits
          behind a disclosure instead of competing with the primary path. */}
      <details className="manual-entry-details">
        <summary>Add one transaction manually</summary>
        <p className="guidance">
          Useful for cash purchases, corrections, or activity without a
          statement export. Positive amounts mean money entered the account;
          negative amounts mean money left it.
        </p>
        <ManualTransactionForm onDataChanged={onDataChanged} />
      </details>

      {error && (
        <div className="inline-error" role="alert">
          {error}
        </div>
      )}

      {outcome && (
        <div className="import-outcome">
          <strong>
            {outcome.totals.inserted > 0
              ? `Imported ${outcome.totals.inserted} new transaction${
                  outcome.totals.inserted === 1 ? "" : "s"
                }`
              : outcome.results.length > 0
                ? "Nothing new — everything in these files was already imported"
                : "No files were imported"}
            {outcome.totals.skipped > 0 &&
              ` · ${outcome.totals.skipped} duplicate${
                outcome.totals.skipped === 1 ? "" : "s"
              } skipped`}
          </strong>
          <ul>
            {outcome.results.map((r) => (
              <li key={r.batch_id}>
                {r.filename} → {r.account_name}: {r.inserted} new
                {r.skipped ? `, ${r.skipped} duplicates` : ""}
                {r.flagged ? `, ${r.flagged} flagged` : ""}
              </li>
            ))}
          </ul>
          {outcome.failures.length > 0 && (
            <div className="inline-error" role="alert">
              {outcome.failures.map((f) => (
                <div key={f.filename}>
                  <strong>{f.filename}</strong> was not imported: {f.message}
                </div>
              ))}
              <p>
                Nothing from a failed file was saved — fix the problem and
                import it again below.
              </p>
            </div>
          )}
          {outcome.totals.flagged > 0 && (
            <p className="file-meta">
              {outcome.totals.flagged} transaction
              {outcome.totals.flagged === 1 ? "" : "s"} were flagged as worth a
              look — the Transactions screen shows them with their categories.
            </p>
          )}
          <div className="button-row">
            <button type="button" onClick={onGoTransactions}>
              See transactions
            </button>
            <button type="button" className="ghost-button" onClick={onGoHome}>
              Back to Home
            </button>
          </div>
        </div>
      )}

      {files.length > 0 && (
        <>
          {accounts.length === 0 && (
            <p className="guidance">
              First, create the account these transactions belong to — every
              import needs a destination so balances stay trustworthy.
            </p>
          )}

          {files.map((file) => (
            <article
              key={file.path}
              className={
                "file-card" +
                (attempted &&
                !file.error &&
                file.tx_count > 0 &&
                !chosen[file.path]
                  ? " needs-account"
                  : "")
              }
            >
              <header>
                <strong>{file.filename}</strong>
                <span className="badge">{file.label}</span>
                {file.confidence !== "high" && (
                  <span className="badge badge-warn">
                    {file.confidence} confidence
                  </span>
                )}
                {file.already_imported && (
                  <span className="badge badge-warn">Already imported</span>
                )}
              </header>

              {file.error ? (
                <><p className="file-error">{file.error}</p>
                {/* A refusal the user cannot answer is worse than a wrong
                    guess. When the dates are genuinely ambiguous, show both
                    readings with the date range each would produce. */}
                {file.date_format_choices.length > 0 && (
                  <div className="date-format-choice">
                    <strong>Which way round are these dates?</strong>
                    <div className="button-row">
                      {file.date_format_choices.map((choice) => (
                        <button
                          key={choice.value}
                          type="button"
                          className={dateFormats[file.path] === choice.value
                            ? "selected ghost-button" : "ghost-button"}
                          disabled={busy !== ""}
                          onClick={() => void chooseDateFormat(file.path, choice.value)}
                        >
                          {choice.label}
                          {choice.example && <small>{choice.example}</small>}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                {/* Naming the bank is far quicker than mapping columns by
                    hand, and it also supplies things a file cannot state
                    about itself, like whether a purchase is written as a
                    positive number. */}
                {file.bank_choices.length > 0 && (
                  <div className="date-format-choice">
                    <strong>Which bank is this from?</strong>
                    <select
                      value={banks[file.path] ?? ""}
                      disabled={busy !== ""}
                      onChange={(e) => void chooseBank(file.path, e.target.value)}
                    >
                      <option value="">Choose your bank…</option>
                      {["CA", "US"].map((country) => (
                        <optgroup key={country} label={country === "CA" ? "Canada" : "United States"}>
                          {file.bank_choices.filter((b) => b.country === country).map((b) => (
                            <option key={b.value} value={b.value}>{b.label}</option>
                          ))}
                        </optgroup>
                      ))}
                    </select>
                  </div>
                )}
                {file.error.toLowerCase().includes("profile") && <button type="button" className="ghost-button" disabled={busy !== ""} onClick={() => void resetProfile(file)}>Reset saved CSV profile</button>}</>
              ) : (
                <>
                  <p className="file-meta">
                    {file.dedup_choice_required
                      ? `${file.tx_count} rows read · one overlap decision needed`
                      : `${file.new_transaction_count} new · ${file.duplicate_count} known duplicate${file.duplicate_count === 1 ? "" : "s"}`}
                    {file.date_start && ` · ${file.date_start} to ${file.date_end}`}
                    {!file.date_start && file.statement_period && ` · ${file.statement_period}`}
                    {file.errors.length > 0 &&
                      ` · ${file.errors.length} parse warning${
                        file.errors.length === 1 ? "" : "s"
                      }`}
                  </p>

                  {file.dedup_choice_required && (
                    <div className="date-format-choice">
                      <strong>Are these updated rows or separate activity?</strong>
                      <p className="guidance">{file.provenance_note}</p>
                      <div className="button-row">
                        <button
                          type="button"
                          className={importModes[file.path] === "reexport"
                            ? "selected ghost-button" : "ghost-button"}
                          disabled={busy !== ""}
                          onClick={() => void chooseImportMode(file.path, "reexport")}
                        >
                          Updated or overlapping export
                          <small>Skip matching rows already in SpendShape.</small>
                        </button>
                        <button
                          type="button"
                          className={importModes[file.path] === "new"
                            ? "selected ghost-button" : "ghost-button"}
                          disabled={busy !== ""}
                          onClick={() => void chooseImportMode(file.path, "new")}
                        >
                          Separate activity
                          <small>Keep matching rows as real repeated purchases.</small>
                        </button>
                      </div>
                    </div>
                  )}
                  <p className="file-meta">
                    Income {moneyCents(file.income)} · Spending {moneyCents(file.spending)}
                  </p>

                  {/* What SpendShape decided about this file, before anything
                      is saved. A wrong guess about dates or decimals is
                      invisible in the charts months later, so it is shown
                      here while it can still be corrected. */}
                  {file.receipt && (
                    <details className="import-receipt">
                      <summary>How SpendShape read this file</summary>
                      <div className="formula-list">
                        <span>Rows read <strong>{file.receipt.rows_parsed}</strong></span>
                        {file.receipt.rows_skipped > 0 && <span>Rows skipped <strong>{file.receipt.rows_skipped}</strong></span>}
                        {file.receipt.zero_value_rows > 0 && <span>Rows with no amount <strong>{file.receipt.zero_value_rows}</strong></span>}
                        <span>Date range <strong>{file.receipt.first_date || "none"} to {file.receipt.last_date || "none"}</strong></span>
                        <span>Months covered <strong>{file.receipt.months_spanned}</strong></span>
                        <span>Money in <strong>{moneyCents(file.receipt.money_in)}</strong></span>
                        <span>Money out <strong>{moneyCents(file.receipt.money_out)}</strong></span>
                        <span>Date format <strong>{file.receipt.date_format}</strong></span>
                        <span>Decimal separator <strong>{file.receipt.decimal_separator}</strong></span>
                        <span>Column separator <strong>{file.receipt.delimiter}</strong></span>
                        {file.receipt.skipped_lines > 0 && <span>Lines above the headings <strong>{file.receipt.skipped_lines}</strong></span>}
                      </div>
                      {file.receipt.notes.map((note) => (
                        <p className="guidance" key={note}>{note}</p>
                      ))}
                    </details>
                  )}

                  {accounts.length > 0 && (
                    <label className="account-choice">
                      Import into
                      <select
                        value={chosen[file.path] ?? ""}
                        disabled={busy !== ""}
                        onChange={(e) => chooseAccount(
                          file.path,
                          e.target.value ? Number(e.target.value) : null,
                        )}
                      >
                        <option value="">Choose an account…</option>
                        {accounts.map((a) => (
                          <option key={a.id} value={a.id}>
                            {a.name} · {a.type_label}
                            {a.institution ? ` · ${a.institution}` : ""}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}

                  {file.csv_profile_id &&
                    (!file.csv_profile_account_type ||
                      (chosen[file.path] && accounts.find((a) => a.id === chosen[file.path])?.type !== file.csv_profile_account_type)) && (
                    <div className="inline-error" role="alert">
                      This saved CSV profile has missing or incompatible account semantics.
                      <button type="button" className="ghost-button" disabled={busy !== ""} onClick={() => void resetProfile(file)}>Reset saved CSV profile</button>
                    </div>
                  )}

                  {file.dedup_pending && (
                    <p className="guidance">
                      Choose an account to check which rows are already in SpendShape.
                    </p>
                  )}

                  {file.mapping_info && (file.needs_mapping || file.mapping || mappingOpen[file.path]) && (
                    <div className="csv-mapping-panel">
                      <div className="mapping-head">
                        <div>
                          <strong>{file.mapping && !mappingOpen[file.path]
                            ? "CSV columns ready"
                            : "Tell SpendShape what each column means"}</strong>
                          <p className="guidance">
                            SpendShape normalizes this preview locally. This screen
                            only chooses columns; SpendShape preserves the values.
                          </p>
                        </div>
                        {file.mapping && !mappingOpen[file.path] && (
                          <button type="button" className="ghost-button" onClick={() =>
                            setMappingOpen((current) => ({ ...current, [file.path]: true }))
                          }>Change columns</button>
                        )}
                      </div>
                      {(file.needs_mapping || mappingOpen[file.path]) && (() => {
                        const draft = mappingDrafts[file.path] ?? file.mapping_info!.suggested;
                        const update = (change: Partial<CsvMapping>) => setMappingDrafts((current) => ({
                          ...current, [file.path]: { ...draft, ...change },
                        }));
                        const headers = file.mapping_info!.headers;
                        const options = <>{headers.map((header) => (
                          <option key={header} value={header}>{header}</option>
                        ))}</>;
                        return (
                          <>
                            <div className="mapping-grid">
                              <label>Date column<select value={draft.date_col} onChange={(e) => update({ date_col: e.target.value })}>
                                <option value="">Choose a column…</option>{options}
                              </select></label>
                              <label>Description or merchant<select value={draft.desc_col} onChange={(e) => update({ desc_col: e.target.value })}>
                                <option value="">Choose a column…</option>{options}
                              </select></label>
                              <label>How amounts are stored<select value={draft.amount_mode} onChange={(e) => update({ amount_mode: e.target.value as CsvMapping["amount_mode"] })}>
                                <option value="">Choose a format…</option>
                                <option value="signed">One amount column (+ / −)</option>
                                <option value="split">Separate money out / money in</option>
                              </select></label>
                              {draft.amount_mode === "signed" ? (
                                <label>Amount column<select value={draft.amount_col} onChange={(e) => update({ amount_col: e.target.value })}>
                                  <option value="">Choose a column…</option>{options}
                                </select><small>Positive means money in. Negative means money out.</small></label>
                              ) : draft.amount_mode === "split" ? <>
                                <label>Money out column<select value={draft.debit_col} onChange={(e) => update({ debit_col: e.target.value })}>
                                  <option value="">Choose a column…</option>{options}
                                </select></label>
                                <label>Money in column<select value={draft.credit_col} onChange={(e) => update({ credit_col: e.target.value })}>
                                  <option value="">Choose a column…</option>{options}
                                </select></label>
                              </> : null}
                            </div>
                            <div className="table-scroll mapping-raw-preview"><table>
                              <thead><tr>{headers.map((header) => <th key={header}>{header}</th>)}</tr></thead>
                              <tbody>{file.mapping_info!.rows.map((row, rowIndex) => (
                                <tr key={rowIndex}>{headers.map((header, cellIndex) => (
                                  <td key={`${header}-${cellIndex}`}>{row[cellIndex] ?? ""}</td>
                                ))}</tr>
                              ))}</tbody>
                            </table></div>
                            <label className="check-row"><input type="checkbox"
                              checked={rememberMapping[file.path] !== false}
                              onChange={(e) => setRememberMapping((current) => ({ ...current, [file.path]: e.target.checked }))} />
                              Remember these columns for future exports from this account
                            </label>
                            <button type="button" disabled={busy !== ""} onClick={() => void applyMapping(file)}>
                              Preview these columns
                            </button>
                          </>
                        );
                      })()}
                    </div>
                  )}

                  {file.sample.length > 0 && (
                    <div className="table-scroll">
                      <table>
                        <thead>
                          <tr>
                            <th>Date</th>
                            <th>Description</th>
                            <th>Category</th>
                            <th className="num">Amount</th>
                          </tr>
                        </thead>
                        <tbody>
                          {file.sample.slice(0, 5).map((row, i) => (
                            <tr key={i}>
                              <td>{row.date}</td>
                              <td>{row.description}</td>
                              <td>{row.category}</td>
                              <td className="num">
                                {moneyCents(row.amount)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {file.tx_count > 5 && (
                        <p className="file-meta">
                          …and {file.tx_count - 5} more
                        </p>
                      )}
                    </div>
                  )}

                  {(() => {
                    const chosenId = chosen[file.path];
                    const account = accounts.find((a) => a.id === chosenId);
                    if (
                      account &&
                      file.suggested_account_type &&
                      account.type !== file.suggested_account_type
                    ) {
                      return (
                        <p className="type-mismatch">
                          Heads up: this looks like a{" "}
                          {typeLabel(file.suggested_account_type).toLowerCase()}{" "}
                          statement, but “{account.name}” is a{" "}
                          {account.type_label.toLowerCase()}. Importing anyway is
                          fine if that’s intentional.
                        </p>
                      );
                    }
                    return null;
                  })()}
                </>
              )}
            </article>
          ))}

          <div className="button-row">
            <button
              type="button"
              onClick={() => void runImport()}
              disabled={busy !== "" || importable.length === 0}
            >
              {busy === "importing"
                ? progress
                  ? `Importing ${progress.done + 1} of ${progress.total} — ${progress.name}`
                  : "Importing…"
                : `Import ${importable.length} file${
                    importable.length === 1 ? "" : "s"
                  }`}
            </button>
            <button
              type="button"
              className="ghost-button"
              disabled={busy !== ""}
              onClick={() => setShowNewAccount((v) => !v)}
            >
              {showNewAccount ? "Hide new account" : "New account…"}
            </button>
          </div>
          {unassigned.length > 0 && (
            <p className="guidance">
              {unassigned.length === importable.length
                ? "Pick an “Import into” account on each file, then import."
                : `${unassigned.length} file${
                    unassigned.length === 1 ? " still needs" : "s still need"
                  } an “Import into” account.`}
            </p>
          )}
        </>
      )}

      {showNewAccount && (
        <div className="new-account-form">
          <strong>Create a new account</strong>
          <p className="guidance">
            Pick the type that matches your bank account or card. This helps
            SpendShape handle balances correctly; it never changes the positive
            or negative signs in your statement.
          </p>
          <div className="form-grid">
            <label>
              Account name
              <input
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="e.g. Everyday Chequing"
              />
            </label>
            <label>
              Type
              <select
                value={newType}
                onChange={(e) => setNewType(e.target.value)}
              >
                {(accountTypes.length
                  ? accountTypes
                  : [{ value: "chequing", label: "Chequing" }]
                ).map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Bank / institution (optional)
              <input
                value={newInstitution}
                onChange={(e) => setNewInstitution(e.target.value)}
              />
            </label>
            <label>
              Opening balance ($)
              <input
                type="number"
                step="100"
                value={newOpening}
                onChange={(e) => setNewOpening(e.target.value)}
              />
            </label>
          </div>
          <div className="button-row">
            <button
              type="button"
              onClick={() => void submitNewAccount()}
              disabled={busy !== "" || !newName.trim()}
            >
              {busy === "creating-account" ? "Creating…" : "Create account"}
            </button>
          </div>
        </div>
      )}

      {files.length === 0 && !outcome && (
        <p className="guidance">
          Choose one or more statement files to see a full preview — type,
          transaction count, and sample rows — before anything is saved.
        </p>
      )}
    </section>
  );
}

export default AddDataView;
