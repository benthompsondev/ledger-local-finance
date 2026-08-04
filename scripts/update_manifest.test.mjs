import assert from "node:assert/strict";
import test from "node:test";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  assertUpdaterArtifact, buildManifest, readSignature, renderManifest,
  validateEntry, validateManifest, REQUIRED_PLATFORMS,
} from "./update_manifest.mjs";

const BASE = "https://github.com/benthompsondev/ledger-local-finance/releases/download/v2.6.0";
// Tauri 2's NSIS bundler signs the installer itself; there is no separate
// archive. Confirmed by building 2.6.0 and reading what it produced.
const ARTIFACT = `${BASE}/NorthstarLedger_2.6.0_x64-setup.exe`;
const SIGNATURE = "dW50cnVzdGVkIGNvbW1lbnQ6IHNpZ25hdHVyZQpSV1FBQkNERUZH";

function manifest(overrides = {}) {
  return {
    version: "2.6.0",
    notes: "Northstar Ledger 2.6.0",
    pub_date: "2026-08-04T00:00:00Z",
    platforms: { "windows-x86_64": { url: ARTIFACT, signature: SIGNATURE } },
    ...overrides,
  };
}

function tempFile(name, contents) {
  const path = join(mkdtempSync(join(tmpdir(), "northstar-manifest-")), name);
  writeFileSync(path, contents);
  return path;
}

// ── the happy path ───────────────────────────────────────────────────────

test("a complete manifest validates", () => {
  assert.doesNotThrow(() => validateManifest(manifest()));
});

test("Windows is the platform Northstar must ship", () => {
  assert.deepEqual(REQUIRED_PLATFORMS, ["windows-x86_64"]);
});

test("rendering is deterministic and ends with a newline", () => {
  const once = renderManifest(manifest());
  const twice = renderManifest(JSON.parse(once));
  assert.equal(once, twice);
  assert.ok(once.endsWith("}\n"));
  assert.deepEqual(Object.keys(JSON.parse(once)), [
    "version", "notes", "pub_date", "platforms",
  ]);
});

// ── the artifact must be the archive, not the installer ──────────────────

test("the signed NSIS installer is the updater artifact", () => {
  assert.doesNotThrow(() => assertUpdaterArtifact(ARTIFACT));
});

test("other bundlers' artifacts are still accepted", () => {
  for (const name of ["app.msi", "app.nsis.zip", "app.zip"]) {
    assert.doesNotThrow(() => assertUpdaterArtifact(`${BASE}/${name}`), name);
  }
});

test("a file the build never signed is refused", () => {
  // Linking the checksum or the release notes gives the user a signature
  // error for what is really a wrong URL.
  for (const name of ["notes.txt", "SHA256SUMS.txt", "latest.json",
                      "app.exe.sig", "README.md"]) {
    assert.throws(() => assertUpdaterArtifact(`${BASE}/${name}`), /never signed|signed updater artifact/, name);
  }
});

// ── everything that must be rejected ─────────────────────────────────────

test("a missing platform is rejected", () => {
  assert.throws(
    () => validateManifest(manifest({ platforms: {} })),
    /missing the required platform "windows-x86_64"/,
  );
});

test("an empty or missing signature is rejected", () => {
  for (const signature of ["", "   ", undefined, null, 42]) {
    assert.throws(
      () => validateEntry("windows-x86_64", { url: ARTIFACT, signature }),
      /signature/,
      String(signature),
    );
  }
});

test("a placeholder signature is rejected", () => {
  for (const signature of ["TODO", "changeme", "xxxx", "none"]) {
    assert.throws(
      () => validateEntry("windows-x86_64", { url: ARTIFACT, signature }),
      /placeholder/,
      signature,
    );
  }
});

test("anything resembling a private key is refused outright", () => {
  assert.throws(
    () => validateEntry("windows-x86_64", {
      url: ARTIFACT,
      signature: "untrusted comment: minisign encrypted secret key\nRWRTY...",
    }),
    /secret-key material/,
  );
});

test("a non-https url is rejected", () => {
  assert.throws(
    () => validateEntry("windows-x86_64", {
      url: ARTIFACT.replace("https://", "http://"), signature: SIGNATURE,
    }),
    /must be https/,
  );
});

test("a url for the wrong version is rejected", () => {
  assert.throws(
    () => validateManifest(manifest({
      platforms: {
        "windows-x86_64": {
          url: ARTIFACT.replace(/2\.6\.0/g, "2.5.5"), signature: SIGNATURE,
        },
      },
    })),
    /does not contain version 2\.6\.0/,
  );
});

test("a manifest version that disagrees with the release is rejected", () => {
  assert.throws(
    () => validateManifest(manifest(), { expectVersion: "2.7.0" }),
    /does not match the release being built/,
  );
  assert.doesNotThrow(
    () => validateManifest(manifest(), { expectVersion: "2.6.0" }),
  );
});

test("a non-semantic version is rejected", () => {
  for (const version of ["2.6", "latest", "", "v2.6.0"]) {
    assert.throws(
      () => validateManifest(manifest({ version })), /semantic version/, version,
    );
  }
});

test("a malformed publication date is rejected", () => {
  for (const pub_date of ["2026-08-04", "yesterday", ""]) {
    assert.throws(
      () => validateManifest(manifest({ pub_date })), /RFC 3339/, pub_date,
    );
  }
});

test("unexpected keys in an entry are rejected", () => {
  assert.throws(
    () => validateEntry("windows-x86_64", {
      url: ARTIFACT, signature: SIGNATURE, sha256: "abc",
    }),
    /unexpected keys sha256/,
  );
});

// ── reading the .sig the build produced ──────────────────────────────────

test("a real signature file is read and trimmed", () => {
  const path = tempFile("app.nsis.zip.sig", `${SIGNATURE}\n`);
  assert.equal(readSignature(path), SIGNATURE);
});

test("an empty signature file means the build was not signed", () => {
  assert.throws(
    () => readSignature(tempFile("app.nsis.zip.sig", "")),
    /empty|unsigned/,
  );
});

test("a whitespace-only signature file is refused", () => {
  assert.throws(
    () => readSignature(tempFile("app.nsis.zip.sig", "\n  \n")),
    /whitespace|empty/,
  );
});

test("a missing signature file names the likely cause", () => {
  assert.throws(
    () => readSignature(join(tmpdir(), "northstar-does-not-exist.sig")),
    /signing key/,
  );
});

// ── building one end to end ──────────────────────────────────────────────

test("buildManifest produces something that validates", () => {
  const built = buildManifest({
    version: "2.6.0",
    notes: "",
    pubDate: "2026-08-04T00:00:00Z",
    platforms: { "windows-x86_64": { url: ARTIFACT, signature: SIGNATURE } },
  });
  assert.equal(built.version, "2.6.0");
  assert.doesNotThrow(() => renderManifest(built));
});
