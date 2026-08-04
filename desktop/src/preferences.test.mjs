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

const { readLandingPage, readWeekStart } = await import("./preferences.ts");
const KEY = "northstar.landingPage";

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
  store.delete("northstar.weekStart");
  assert.equal(readWeekStart(), "monday");
  store.set("northstar.weekStart", "sunday");
  assert.equal(readWeekStart(), "sunday");
});
