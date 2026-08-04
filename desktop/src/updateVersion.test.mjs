import assert from "node:assert/strict";
import test from "node:test";

import {
  compareVersions, isNewerVersion, progressText, updateErrorMessage,
} from "./updateVersion.ts";

// ── ordering ─────────────────────────────────────────────────────────────

test("plain releases order by number, not by string", () => {
  assert.equal(compareVersions("2.6.0", "2.5.5"), 1);
  assert.equal(compareVersions("2.5.5", "2.6.0"), -1);
  assert.equal(compareVersions("2.6.0", "2.6.0"), 0);
  // The string trap: "2.10.0" < "2.9.0" alphabetically.
  assert.equal(compareVersions("2.10.0", "2.9.0"), 1);
  assert.equal(compareVersions("10.0.0", "9.9.9"), 1);
});

test("a v prefix is accepted on either side", () => {
  assert.equal(compareVersions("v2.6.0", "2.6.0"), 0);
  assert.equal(compareVersions("2.6.0", "V2.6.0"), 0);
  assert.equal(compareVersions("v2.6.1", "v2.6.0"), 1);
});

test("build metadata is ignored", () => {
  assert.equal(compareVersions("2.6.0+build.7", "2.6.0"), 0);
});

test("a prerelease sorts below its own release", () => {
  assert.equal(compareVersions("2.6.0-beta.1", "2.6.0"), -1);
  assert.equal(compareVersions("2.6.0", "2.6.0-rc.1"), 1);
});

test("prereleases order among themselves", () => {
  assert.equal(compareVersions("2.6.0-beta.2", "2.6.0-beta.1"), 1);
  assert.equal(compareVersions("2.6.0-beta.10", "2.6.0-beta.9"), 1);
  assert.equal(compareVersions("2.6.0-alpha", "2.6.0-beta"), -1);
  // Numeric identifiers rank below alphanumeric ones.
  assert.equal(compareVersions("2.6.0-1", "2.6.0-alpha"), -1);
  // More fields wins when the shared prefix matches.
  assert.equal(compareVersions("2.6.0-beta.1", "2.6.0-beta"), 1);
});

test("nonsense is refused rather than guessed at", () => {
  for (const bad of ["", "2.6", "2.6.0.1", "two.six.zero", "2.x.0", "-1.0.0"]) {
    assert.throws(() => compareVersions(bad, "2.6.0"), undefined, bad);
  }
});

// ── the upgrade decision ─────────────────────────────────────────────────

test("only a genuinely newer version is an upgrade", () => {
  assert.equal(isNewerVersion("2.6.0", "2.5.5"), true);
  assert.equal(isNewerVersion("2.5.5", "2.5.5"), false);
  assert.equal(isNewerVersion("2.5.4", "2.5.5"), false);
  assert.equal(isNewerVersion("v2.6.0", "2.5.5"), true);
});

test("a prerelease never overwrites the release it precedes", () => {
  assert.equal(isNewerVersion("2.6.0-rc.1", "2.6.0"), false);
  assert.equal(isNewerVersion("2.6.0-rc.1", "2.5.5"), true);
});

test("a malformed version from GitHub is never an upgrade", () => {
  // GitHub is the untrusted side of this exchange. Unparseable means no.
  for (const bad of ["", "latest", "2.6", "../../etc", "9999999999999999"]) {
    assert.equal(isNewerVersion(bad, "2.5.5"), false, bad);
  }
});

// ── what a person is told ────────────────────────────────────────────────

test("a signature failure says plainly that nothing was installed", () => {
  for (const detail of [
    "signature verification failed",
    "invalid public key",
    "minisign: untrusted comment mismatch",
  ]) {
    const message = updateErrorMessage(new Error(detail), "install");
    assert.match(message, /not be verified as genuine/i);
    assert.match(message, /nothing was installed/i);
  }
});

test("a broken manifest is distinguished from a broken network", () => {
  const manifest = updateErrorMessage(new Error("failed to parse JSON"), "check");
  assert.match(manifest, /could not be read/i);

  const network = updateErrorMessage(new Error("connection timed out"), "check");
  assert.match(network, /GitHub could not be reached/i);
  assert.match(network, /try again/i);
});

test("a missing platform entry tells the user to look at releases", () => {
  const message = updateErrorMessage(
    new Error("the platform was not found on the response"), "check",
  );
  assert.match(message, /no update is published for this platform/i);
});

test("an unrecognised failure still promises nothing was changed", () => {
  const check = updateErrorMessage(new Error("something odd"), "check");
  const install = updateErrorMessage(new Error("something odd"), "install");
  assert.match(check, /nothing was sent or changed/i);
  assert.match(install, /untouched/i);
});

test("a non-Error rejection is handled", () => {
  assert.ok(updateErrorMessage("plain string failure", "check").length > 0);
  assert.ok(updateErrorMessage(undefined, "install").length > 0);
});

// ── progress ─────────────────────────────────────────────────────────────

test("progress reports a percentage when the size is known", () => {
  assert.equal(progressText(0, 100), "Downloading… 0%");
  assert.equal(progressText(50, 100), "Downloading… 50%");
  assert.equal(progressText(100, 100), "Downloading… 100%");
});

test("progress never invents a percentage it does not have", () => {
  assert.equal(progressText(500), "Downloading…");
  assert.equal(progressText(500, 0), "Downloading…");
});

test("progress cannot exceed 100 or go negative", () => {
  assert.equal(progressText(150, 100), "Downloading… 100%");
  assert.equal(progressText(-10, 100), "Downloading… 0%");
});
