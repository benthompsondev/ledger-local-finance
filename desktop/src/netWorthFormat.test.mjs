import assert from "node:assert/strict";
import test from "node:test";

import { describeSpan, signedAmount } from "./netWorthFormat.ts";

const money = (value) => `$${value.toFixed(2)}`;

test("an absent comparison is not a zero", () => {
  // The whole defect: `?? 0` rendered +$0.00 for a month that was never
  // recorded, which reads as "you held steady".
  assert.equal(signedAmount(null, money), null);
  assert.equal(signedAmount(undefined, money), null);
});

test("a real zero is still a real zero", () => {
  assert.equal(signedAmount(0, money), "+$0.00");
});

test("signs are explicit in both directions", () => {
  assert.equal(signedAmount(1250.5, money), "+$1250.50");
  assert.equal(signedAmount(-1250.5, money), "−$1250.50");
});

test("a span is calendar months, not a row count", () => {
  assert.equal(describeSpan(1), "1 month");
  assert.equal(describeSpan(6), "6 months");
  assert.equal(describeSpan(0), "the same month");
});

test("a long span reads in years", () => {
  assert.equal(describeSpan(12), "1 year");
  assert.equal(describeSpan(13), "1 year and 1 month");
  assert.equal(describeSpan(26), "2 years and 2 months");
});

test("a nonsense span does not produce nonsense text", () => {
  assert.equal(describeSpan(-3), "the same month");
  assert.equal(describeSpan(1.4), "1 month");
});
