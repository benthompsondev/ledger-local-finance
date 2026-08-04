import { useState } from "react";
import { relaunch } from "@tauri-apps/plugin-process";

import {
  checkForUpdate, installUpdate, progressText, updateErrorMessage,
  type Update,
} from "./updater";

export const RELEASES_URL =
  "https://github.com/benthompsondev/ledger-local-finance/releases/latest";

/** Every state this panel can be in. `error` carries wording a person can
 *  act on, including the two that matter most: a signature that did not
 *  verify, and a manifest that could not be read. */
type UpdateState =
  | { kind: "idle" }
  | { kind: "checking" }
  | { kind: "latest" }
  | { kind: "available"; version: string; notes: string }
  | { kind: "installing"; downloaded: number; total?: number }
  | { kind: "restart" }
  | { kind: "error"; message: string };

interface Props {
  version: string;
}

/**
 * Checking for updates is the one thing in Northstar that reaches the
 * internet without an AI key configured, so it never happens on its own.
 * There is no check on mount, no timer, and no retry loop: this component
 * makes exactly one request per click.
 */
export function UpdatePanel({ version }: Props) {
  const [state, setState] = useState<UpdateState>({ kind: "idle" });
  const [pending, setPending] = useState<Update | null>(null);

  const checkNow = async () => {
    setState({ kind: "checking" });
    try {
      // Release the previous handle before asking again, so a second check
      // cannot leave a half-open download behind.
      if (pending) await pending.close();
      const update = await checkForUpdate();
      setPending(update);
      setState(update
        ? { kind: "available", version: update.version, notes: update.body ?? "" }
        : { kind: "latest" });
    } catch (cause) {
      setPending(null);
      setState({ kind: "error", message: updateErrorMessage(cause, "check") });
    }
  };

  const downloadAndInstall = async () => {
    if (!pending) return;
    let downloaded = 0;
    let total: number | undefined;
    setState({ kind: "installing", downloaded });
    try {
      await installUpdate(pending, (event) => {
        if (event.event === "Started") {
          total = event.data.contentLength;
        } else if (event.event === "Progress") {
          downloaded += event.data.chunkLength;
        } else {
          downloaded = total ?? downloaded;
        }
        setState({ kind: "installing", downloaded, total });
      });
      setState({ kind: "restart" });
    } catch (cause) {
      setState({ kind: "error", message: updateErrorMessage(cause, "install") });
    }
  };

  return (
    <article className="form-card" id="app-updates">
      <h3>App updates</h3>
      <p className="guidance">
        Northstar checks GitHub only when you press the button below, and only
        for the version number of the latest release. Nothing about you, your
        accounts or your transactions is sent, and no check happens on its own
        or in the background.
      </p>
      <dl>
        <div><dt>Installed</dt><dd>Northstar Ledger {version || "…"}</dd></div>
      </dl>

      <div className="button-row" aria-live="polite">
        {state.kind === "idle" && (
          <button type="button" onClick={() => void checkNow()}>
            Check for updates
          </button>
        )}

        {state.kind === "checking" && <p className="guidance">Checking GitHub…</p>}

        {state.kind === "latest" && (
          <>
            <p className="success-text">
              You are on the latest version ({version}).
            </p>
            <button type="button" className="ghost-button"
              onClick={() => void checkNow()}>Check again</button>
          </>
        )}

        {state.kind === "available" && (
          <>
            <p className="success-text">
              Version {state.version} is available.
            </p>
            <button type="button" onClick={() => void downloadAndInstall()}>
              Download and install
            </button>
            <button type="button" className="ghost-button"
              onClick={() => setState({ kind: "idle" })}>Not now</button>
          </>
        )}

        {state.kind === "installing" && (
          <p className="guidance">{progressText(state.downloaded, state.total)}</p>
        )}

        {state.kind === "restart" && (
          <>
            <p className="success-text">
              The update is installed and verified.
            </p>
            <button type="button" onClick={() => void relaunch()}>
              Restart Northstar
            </button>
          </>
        )}

        {state.kind === "error" && (
          <>
            <p className="warning-text">{state.message}</p>
            <button type="button" className="ghost-button"
              onClick={() => void checkNow()}>Try again</button>
          </>
        )}
      </div>

      {state.kind === "available" && state.notes && (
        <details className="formula-details">
          <summary>What changed in {state.version}</summary>
          <p className="guidance">{state.notes}</p>
        </details>
      )}

      <p className="file-meta">
        Updates are signed. Northstar installs one only if the signature
        matches its built-in key, so a download that was tampered with or
        interrupted is refused and your current version is left alone.
        Releases are also listed at{" "}
        <a href={RELEASES_URL} target="_blank" rel="noreferrer">GitHub</a>.
      </p>
    </article>
  );
}

export default UpdatePanel;
