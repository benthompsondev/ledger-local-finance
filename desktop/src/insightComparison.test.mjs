import assert from "node:assert/strict";
import test from "node:test";

import { cashflowExtremes, rollingAverageLabel } from "./insightComparison.ts";
import { signedAmount } from "./netWorthFormat.ts";

test("rolling averages name the months actually included", () => {
  assert.equal(rollingAverageLabel(3), "3-month same-day average");
  assert.equal(rollingAverageLabel(2), "2-month same-day average");
  assert.equal(rollingAverageLabel(2, true), "2-mo avg");
});

test("invalid counts do not invent a three-month baseline", () => {
  assert.equal(rollingAverageLabel(undefined), "Recent same-day average");
  assert.equal(rollingAverageLabel(Number.NaN, true), "Recent avg");
});

test("an all-negative run keeps the actual signed best month", () => {
  const { best, weakest } = cashflowExtremes([
    { month: "2026-05", net: -240 },
    { month: "2026-06", net: -68 },
    { month: "2026-07", net: -310 },
  ]);
  const money = (value) => `$${value.toFixed(2)}`;

  assert.deepEqual(best, { month: "2026-06", net: -68 });
  assert.deepEqual(weakest, { month: "2026-07", net: -310 });
  assert.equal(signedAmount(best.net, money), "−$68.00");
});
