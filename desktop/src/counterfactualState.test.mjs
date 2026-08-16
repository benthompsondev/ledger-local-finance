import assert from "node:assert/strict";
import test from "node:test";

import { visibleReplay } from "./counterfactualState.ts";

const target = {
  kind: "merchant",
  key: "DEMO GROCERY",
  label: "Demo Grocery",
  amount: 40,
  tx_count: 1,
};

const replay = {
  available: true,
  reason: "",
  target,
  reduction_pct: 50,
};

const data = {
  available: true,
  reason: "",
  months: [],
  month: "2026-07",
  month_label: "July 2026",
  targets: [target],
  replay,
};

const selection = { month: "2026-07", target, pct: 50 };

test("shows only the replay that matches the current controls", () => {
  assert.equal(visibleReplay(data, "", selection), replay);
  assert.equal(visibleReplay(data, "", { ...selection, month: "2026-06" }), null);
  assert.equal(visibleReplay(data, "", { ...selection, pct: 25 }), null);
  assert.equal(visibleReplay(data, "", {
    ...selection,
    target: { ...target, key: "DEMO MARKET" },
  }), null);
});

test("hides the previous financial result when the latest request failed", () => {
  assert.equal(visibleReplay(data, "sidecar failed", selection), null);
});

test("keeps a current engine refusal visible", () => {
  const unavailable = { available: false, reason: "Nothing to replay." };
  assert.equal(
    visibleReplay({ ...data, replay: unavailable }, "", selection),
    unavailable,
  );
});
