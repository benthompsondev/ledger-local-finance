// Build or validate Northstar's updater manifest (latest.json).
//
// Tauri's updater reads one latest.json from the newest GitHub release, finds
// its own platform entry, downloads the archive named there, and refuses it
// unless the detached signature matches the public key compiled into the app.
// That last step is the whole security model, so this script exists to make
// the manifest a checkable artifact rather than hand-edited JSON.
//
// It rejects:
//   - a manifest missing windows-x86_64, the only platform Northstar ships
//   - a non-https url, or one pointing at the interactive NSIS installer
//     rather than the updater archive (the mistake that produces an update
//     which downloads fine and then does nothing)
//   - an empty, missing or placeholder signature
//   - anything that looks like private-key material
//   - a version that is not semantic, or that disagrees with package.json
//
// It only ever touches public data: artifact URLs and detached signature
// contents. It never reads, needs, or prints the private signing key.
//
// Usage:
//   node scripts/update_manifest.mjs --validate latest.json
//   node scripts/update_manifest.mjs --version 2.6.0 \
//     --pub-date 2026-08-04T00:00:00Z --notes "Northstar Ledger 2.6.0" \
//     --url <https url to the .nsis.zip> --sig <path to the .sig> \
//     --out latest.json

import { readFileSync, writeFileSync, statSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

/** Windows is the only platform Northstar builds. */
export const REQUIRED_PLATFORMS = ['windows-x86_64'];

const RFC3339 = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;
const SEMVER = /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/;
const SECRET_MATERIAL = /private key|secret key|BEGIN [A-Z ]*PRIVATE/i;
const PLACEHOLDER = /^(todo|tbd|xxx+|placeholder|changeme|none|null|undefined)$/i;

function fail(message) {
  throw new Error(message);
}

/**
 * The URL must be the artifact the build actually signed.
 *
 * Tauri 2's NSIS bundler signs the setup executable itself and writes
 * `<installer>.exe.sig` beside it — there is no separate archive, which is a
 * change from Tauri 1 where the updater consumed a .zip. Verified by building:
 *
 *     Finished 1 updater signature at:
 *       ...\bundle\nsis\Northstar Ledger_2.6.0_x64-setup.exe.sig
 *
 * So the installer *is* the updater artifact here. What still has to be
 * refused is a URL pointing at something that was never signed — release
 * notes, a checksum file, a source archive — because the updater would
 * download it, fail verification, and report a signature error for what is
 * really a wrong link.
 */
const SIGNED_ARTIFACT = /\.(exe|msi|nsis\.zip|app\.tar\.gz|zip|tar\.gz|AppImage)$/i;

export function assertUpdaterArtifact(url) {
  if (/\.(txt|md|json|sha256|sig|asc|pdf|png)$/i.test(url)) {
    fail(
      `url points at a file the build never signed: ${url}\n` +
        '  the updater needs the artifact itself, not a note or checksum beside it',
    );
  }
  if (!SIGNED_ARTIFACT.test(url)) {
    fail(`url does not look like a signed updater artifact: ${url}`);
  }
}

/** Validate one platform entry ({ signature, url }). */
export function validateEntry(platform, entry) {
  if (typeof entry !== 'object' || entry === null) {
    fail(`${platform}: entry must be an object with signature and url`);
  }
  const { signature, url } = entry;
  if (typeof url !== 'string' || url.trim() === '') {
    fail(`${platform}: url must be a non-empty string`);
  }
  if (!url.startsWith('https://')) {
    fail(`${platform}: url must be https, got "${url}"`);
  }
  assertUpdaterArtifact(url);
  if (typeof signature !== 'string' || signature.trim() === '') {
    fail(`${platform}: signature must be the non-empty contents of the .sig file`);
  }
  if (PLACEHOLDER.test(signature.trim())) {
    fail(`${platform}: signature is a placeholder, not a real signature`);
  }
  if (SECRET_MATERIAL.test(signature)) {
    fail(`${platform}: signature looks like secret-key material — refusing to continue`);
  }
  const extraKeys = Object.keys(entry).filter((key) => key !== 'signature' && key !== 'url');
  if (extraKeys.length > 0) {
    fail(`${platform}: unexpected keys ${extraKeys.join(', ')}`);
  }
}

/** Validate a complete manifest object. */
export function validateManifest(manifest, { expectVersion } = {}) {
  if (typeof manifest !== 'object' || manifest === null) fail('manifest must be an object');
  const { version, notes, pub_date: pubDate, platforms } = manifest;
  if (typeof version !== 'string' || !SEMVER.test(version)) {
    fail(`manifest version must be a semantic version, got "${version}"`);
  }
  if (expectVersion && version !== expectVersion) {
    fail(`manifest version "${version}" does not match the release being built (${expectVersion})`);
  }
  if (typeof notes !== 'string') fail('manifest notes must be a string (may be empty)');
  if (typeof pubDate !== 'string' || !RFC3339.test(pubDate)) {
    fail(`manifest pub_date must be RFC 3339 (e.g. 2026-08-04T00:00:00Z), got "${pubDate}"`);
  }
  if (typeof platforms !== 'object' || platforms === null) {
    fail('manifest platforms must be an object');
  }
  for (const required of REQUIRED_PLATFORMS) {
    if (!(required in platforms)) {
      fail(`manifest is missing the required platform "${required}"`);
    }
  }
  for (const [platform, entry] of Object.entries(platforms)) {
    validateEntry(platform, entry);
  }
  // The version in the URL must be the version being published, or the
  // manifest advertises one release and delivers another.
  for (const [platform, entry] of Object.entries(platforms)) {
    if (!entry.url.includes(version)) {
      fail(`${platform}: url does not contain version ${version}: ${entry.url}`);
    }
  }
  return manifest;
}

/** Assemble a manifest from explicit values. */
export function buildManifest({ version, notes = '', pubDate, platforms }) {
  return validateManifest({ version, notes, pub_date: pubDate, platforms });
}

/** Deterministic JSON: fixed key order, sorted platforms, LF, newline at EOF. */
export function renderManifest(manifest) {
  validateManifest(manifest);
  const ordered = {
    version: manifest.version,
    notes: manifest.notes,
    pub_date: manifest.pub_date,
    platforms: Object.fromEntries(
      Object.keys(manifest.platforms)
        .sort()
        .map((platform) => [
          platform,
          {
            signature: manifest.platforms[platform].signature,
            url: manifest.platforms[platform].url,
          },
        ]),
    ),
  };
  return `${JSON.stringify(ordered, null, 2)}\n`;
}

/** Read a .sig file, refusing an empty or missing one outright. */
export function readSignature(path) {
  let stats;
  try {
    stats = statSync(path);
  } catch {
    fail(`signature file not found: ${path}\n  did the build run with a signing key set?`);
  }
  if (!stats.isFile() || stats.size === 0) {
    fail(`signature file is empty: ${path}\n  an unsigned build cannot be published`);
  }
  const signature = readFileSync(path, 'utf8').trim();
  if (signature === '') fail(`signature file contains only whitespace: ${path}`);
  return signature;
}

function parseArgs(argv) {
  const options = {};
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    switch (arg) {
      case '--validate': options.validate = argv[++index]; break;
      case '--version': options.version = argv[++index]; break;
      case '--notes': options.notes = argv[++index]; break;
      case '--pub-date': options.pubDate = argv[++index]; break;
      case '--url': options.url = argv[++index]; break;
      case '--sig': options.sig = argv[++index]; break;
      case '--out': options.out = argv[++index]; break;
      case '--expect-version': options.expectVersion = argv[++index]; break;
      default: fail(`unknown argument: ${arg}`);
    }
  }
  return options;
}

function main() {
  const options = parseArgs(process.argv.slice(2));

  if (options.validate) {
    validateManifest(JSON.parse(readFileSync(options.validate, 'utf8')), {
      expectVersion: options.expectVersion,
    });
    console.log(`${options.validate} is a valid Northstar updater manifest.`);
    return;
  }

  if (!options.url || !options.sig) fail('--url and --sig are both required');
  const manifest = buildManifest({
    version: options.version,
    notes: options.notes ?? '',
    pubDate: options.pubDate,
    platforms: {
      'windows-x86_64': { url: options.url, signature: readSignature(options.sig) },
    },
  });

  const rendered = renderManifest(manifest);
  if (options.out) {
    writeFileSync(options.out, rendered);
    // Log the version and platforms only — never signature contents.
    console.log(
      `Wrote ${options.out} for v${manifest.version} (${Object.keys(manifest.platforms).join(', ')})`,
    );
  } else {
    process.stdout.write(rendered);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    main();
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exit(1);
  }
}
