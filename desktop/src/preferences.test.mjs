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
  },
  dispatchEvent: () => {},
};

const { readLandingPage, readWeekStart, migrateLegacyPreferences }
  = await import("./preferences.ts");
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

// ── the SpendShape rename ────────────────────────────────────────────────
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
