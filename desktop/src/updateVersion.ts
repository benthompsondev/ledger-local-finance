// Version comparison and error wording for the updater.
//
// Deliberately free of Tauri imports so it can be run directly by
// `node --test`. The half that actually talks to the updater plugin lives in
// updater.ts and calls into here.

interface ParsedVersion {
  core: [number, number, number];
  prerelease: string[] | null;
}

/** Parse a semantic version, tolerating a leading v and ignoring build metadata. */
function parseVersion(value: string): ParsedVersion {
  const withoutPrefix = value.trim().replace(/^v/i, "");
  const buildIndex = withoutPrefix.indexOf("+");
  const withoutBuild = buildIndex === -1
    ? withoutPrefix : withoutPrefix.slice(0, buildIndex);
  const prereleaseIndex = withoutBuild.indexOf("-");
  const coreText = prereleaseIndex === -1
    ? withoutBuild : withoutBuild.slice(0, prereleaseIndex);
  const prereleaseText = prereleaseIndex === -1
    ? undefined : withoutBuild.slice(prereleaseIndex + 1);
  const coreParts = coreText.split(".");

  if (coreParts.length !== 3 || coreParts.some((part) => !/^\d+$/.test(part))) {
    throw new Error(`Invalid semantic version: ${value}`);
  }

  return {
    core: coreParts.map(Number) as [number, number, number],
    prerelease: prereleaseText ? prereleaseText.split(".") : null,
  };
}

/** SemVer rule: a version with a prerelease tag sorts *below* the same
 *  release without one, so 2.6.0-beta.1 must never look newer than 2.6.0. */
function comparePrerelease(left: string[] | null, right: string[] | null): number {
  if (left === null && right === null) return 0;
  if (left === null) return 1;
  if (right === null) return -1;

  const length = Math.max(left.length, right.length);
  for (let index = 0; index < length; index += 1) {
    const a = left[index];
    const b = right[index];
    if (a === undefined) return -1;
    if (b === undefined) return 1;
    if (a === b) continue;

    const aNumber = /^\d+$/.test(a) ? Number(a) : null;
    const bNumber = /^\d+$/.test(b) ? Number(b) : null;
    if (aNumber !== null && bNumber !== null) return Math.sign(aNumber - bNumber);
    if (aNumber !== null) return -1;
    if (bNumber !== null) return 1;
    return a < b ? -1 : 1;
  }

  return 0;
}

/** -1, 0 or 1. Throws on anything that is not a semantic version. */
export function compareVersions(left: string, right: string): number {
  const a = parseVersion(left);
  const b = parseVersion(right);

  for (let index = 0; index < a.core.length; index += 1) {
    if (a.core[index] !== b.core[index]) {
      return Math.sign(a.core[index] - b.core[index]);
    }
  }

  return comparePrerelease(a.prerelease, b.prerelease);
}

/** Whether `candidate` is genuinely newer than what is installed.
 *
 *  A malformed version is not an upgrade. GitHub is the untrusted side of
 *  this exchange, so anything unparseable is refused rather than guessed at.
 */
export function isNewerVersion(candidate: string, installed: string): boolean {
  try {
    return compareVersions(candidate, installed) > 0;
  } catch {
    return false;
  }
}

export type UpdateAction = "check" | "install";

/** Turn a plugin error into something a person can act on.
 *
 *  The signature case is first and is deliberately unambiguous: a failed
 *  signature check means nothing was installed, and saying so is the whole
 *  point of signing the artifact in the first place.
 */
export function updateErrorMessage(error: unknown, action: UpdateAction): string {
  const detail = error instanceof Error ? error.message : String(error);

  if (/signature|public key|key id|verify|minisign|untrusted/i.test(detail)) {
    return "That update could not be verified as genuine, so nothing was "
      + "installed. Your copy of SignalSpace is unchanged.";
  }

  if (/json|parse|manifest|malformed|unexpected token|invalid/i.test(detail)) {
    return "The update information from GitHub could not be read. Nothing was "
      + "installed. Try again later, or download the latest release manually.";
  }

  if (
    /platform.*(was )?not found|no (matching )?platform|unsupported platform|target.*not (found|available)/i
      .test(detail)
  ) {
    return "No update is published for this platform yet. Check the releases "
      + "page on GitHub for the latest download.";
  }

  if (/network|fetch|dns|connection|timed? ?out|http|404|offline|unreachable/i.test(detail)) {
    return "GitHub could not be reached. Check your connection and try again. "
      + "Nothing was downloaded or changed.";
  }

  return action === "check"
    ? "SignalSpace could not check for updates. Nothing was sent or changed."
    : "SignalSpace could not install that update. Your current version is "
      + "untouched.";
}

/** Human-readable download progress. Unknown total is a real case: some
 *  servers omit content-length, and inventing a percentage would be a lie. */
export function progressText(downloaded: number, total?: number): string {
  if (!total || total <= 0) return "Downloading…";
  const percent = Math.min(100, Math.max(0, Math.round((downloaded / total) * 100)));
  return `Downloading… ${percent}%`;
}
