import assert from "node:assert/strict";
import test from "node:test";

// preferences.ts talks to window.localStorage directly. A tiny stand-in is
// cheaper and clearer here than pulling in a DOM test framework for two
// functions, and it keeps this file in the same shape as chartScale.test.mjs.
const store = new Map();
globalThis.window = {
  localStorage: {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
    // clearSignalSpacePreferences walks the whole store to find the
    // per-profile keys, so the stand-in has to be enumerable the way real
    // localStorage is.
    get length() { return store.size; },
    key: (i) => [...store.keys()][i] ?? null,
  },
  dispatchEvent: () => {},
};

const {
  readLandingPage, readWeekStart, migrateLegacyPreferences,
  readGettingStartedDismissed, saveGettingStartedDismissed,
  clearSignalSpacePreferences,
} = await import("./preferences.ts");
const KEY = "spendshape.landingPage";

test("a saved Goals startup page migrates to Home", () => {
  store.set(KEY, "goals");
  assert.equal(readLandingPage(), "home");
  // Migrated, not merely ignored: the stored value must stop pointing at a
  // screen that is no longer part of the product.
  assert.equal(store.get(KEY), "home");
});

test("every other saved startup page is left alone", () => {
  for (const page of ["home", "plan", "insights", "transactions"]) {
    store.set(KEY, page);
    assert.equal(readLandingPage(), page);
    assert.equal(store.get(KEY), page);
  }
});

test("an unknown or absent startup page falls back to Home", () => {
  store.delete(KEY);
  assert.equal(readLandingPage(), "home");
  store.set(KEY, "something-retired");
  assert.equal(readLandingPage(), "home");
});

test("weeks start on Monday unless Sunday was chosen", () => {
  store.delete("spendshape.weekStart");
  assert.equal(readWeekStart(), "monday");
  store.set("spendshape.weekStart", "sunday");
  assert.equal(readWeekStart(), "sunday");
});

// ── the SignalSpace rename ────────────────────────────────────────────────
// The storage namespace moved from northstar.* to spendshape.*. Without the
// migration below, upgrading would silently reset every preference and look
// like the update had lost them.

test("preferences saved under the old name survive the rename", () => {
  store.clear();
  store.set("northstar.landingPage", "insights");
  store.set("northstar.weekStart", "sunday");
  store.set("northstar.density", "compact");

  migrateLegacyPreferences();

  assert.equal(store.get("spendshape.landingPage"), "insights");
  assert.equal(store.get("spendshape.weekStart"), "sunday");
  assert.equal(store.get("spendshape.density"), "compact");
  assert.equal(readLandingPage(), "insights");
  assert.equal(readWeekStart(), "sunday");
});

test("the old keys are removed, so the migration runs only once", () => {
  store.clear();
  store.set("northstar.landingPage", "plan");
  migrateLegacyPreferences();
  assert.equal(store.has("northstar.landingPage"), false);
});

test("a choice made after upgrading is never overwritten by an old one", () => {
  store.clear();
  store.set("northstar.landingPage", "insights");
  store.set("spendshape.landingPage", "transactions");
  migrateLegacyPreferences();
  assert.equal(store.get("spendshape.landingPage"), "transactions");
  assert.equal(store.has("northstar.landingPage"), false);
});

test("nothing stored under either name still yields the defaults", () => {
  store.clear();
  migrateLegacyPreferences();
  assert.equal(readLandingPage(), "home");
  assert.equal(readWeekStart(), "monday");
});

// ── the getting-started card ──────────────────────────────────────────────
// The card is for a profile holding nothing, and "I have already seen this"
// is true of the person who has been using their finances for months and not
// of the empty profile they just created for somebody else. That makes the
// dismissal per profile rather than per installation, which is worth testing
// rather than asserting about the source.

test("a profile that has not dismissed the guide still gets it", () => {
  store.clear();
  assert.equal(readGettingStartedDismissed("default"), false);
});

test("dismissing the guide is remembered for that profile", () => {
  store.clear();
  saveGettingStartedDismissed("default", true);
  assert.equal(readGettingStartedDismissed("default"), true);
});

test("one profile's dismissal never silences another", () => {
  store.clear();
  saveGettingStartedDismissed("default", true);
  // The whole point of the per-profile key: an empty profile created for
  // somebody else must still be shown what to do.
  assert.equal(readGettingStartedDismissed("p2"), false);
});

test("re-opening the guide clears the stored answer", () => {
  store.clear();
  saveGettingStartedDismissed("default", true);
  saveGettingStartedDismissed("default", false);
  assert.equal(readGettingStartedDismissed("default"), false);
  assert.equal(store.size, 0, "re-opening should remove the key, not blank it");
});

test("an empty profile id is never written and never reads as dismissed", () => {
  store.clear();
  // A missing id must not write one shared key that every profile then reads
  // as "already dismissed".
  saveGettingStartedDismissed("", true);
  assert.equal(store.size, 0);
  assert.equal(readGettingStartedDismissed(""), false);
});

test("a complete reset clears every profile's dismissal", () => {
  store.clear();
  saveGettingStartedDismissed("default", true);
  saveGettingStartedDismissed("p2", true);
  saveGettingStartedDismissed("p3", true);
  store.set("spendshape.weekStart", "sunday");

  clearSignalSpacePreferences();

  assert.equal(readGettingStartedDismissed("default"), false);
  assert.equal(readGettingStartedDismissed("p2"), false);
  assert.equal(readGettingStartedDismissed("p3"), false);
  assert.equal(readWeekStart(), "monday");
  assert.equal(store.size, 0, "a reset that leaves keys behind is not a reset");
});
