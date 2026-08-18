// The half of the updater that talks to Tauri.
//
// Nothing here runs on its own. Every call below happens because the user
// pressed a button in Settings: SignalSpace makes no update request at launch,
// on a timer, or in the background, and sends nothing about the person or
// their finances when it does make one. The request is an ordinary HTTPS GET
// for a public release manifest.
import { check, type DownloadEvent, type Update } from "@tauri-apps/plugin-updater";
import { getVersion } from "@tauri-apps/api/app";

import { isNewerVersion } from "./updateVersion";

/** Give up rather than hang on a slow or captive network. */
const CHECK_TIMEOUT_MS = 15_000;
const INSTALL_TIMEOUT_MS = 300_000;

/**
 * Ask GitHub whether a newer SignalSpace exists.
 *
 * Returns null when the installed version is already current. The plugin
 * verifies the manifest signature itself; the extra version comparison here
 * guards the case where a manifest advertises something that is not actually
 * newer, so a republished or rolled-back release cannot walk someone
 * backwards.
 */
export async function checkForUpdate(): Promise<Update | null> {
  const update = await check({ timeout: CHECK_TIMEOUT_MS });
  if (!update) return null;

  const installed = await getVersion();
  if (!isNewerVersion(update.version, installed)) {
    await update.close();
    return null;
  }
  return update;
}

/**
 * Download and install, reporting progress.
 *
 * The signature is checked before anything is written. A failure here leaves
 * the installed application exactly as it was.
 */
export async function installUpdate(
  update: Update,
  onEvent: (event: DownloadEvent) => void,
): Promise<void> {
  await update.downloadAndInstall(onEvent, { timeout: INSTALL_TIMEOUT_MS });
}

export { updateErrorMessage, progressText } from "./updateVersion";
export type { Update, DownloadEvent };
