import assert from "node:assert/strict";
import test from "node:test";

import {
  baselineOf, canCompare, categoryCaption, differenceOf,
} from "./categoryComparison.ts";

const money = (value) => `$${value.toFixed(2)}`;

/** What the engine sends for a month it cannot compare against: the amount
 *  and the share are real, the baseline is absent rather than zero. */
const uncomparable = [
  { amount: 84.2, share: 60, previous: null, delta: null },
  { amount: 56.13, share: 40, previous: null, delta: null },
];

const comparable = [
  { amount: 84.2, share: 60, previous: 70.0, delta: 14.2 },
  { amount: 56.13, share: 40, previous: 90.0, delta: -33.87 },
];

test("a named previous month is not on its own a baseline", () => {
  // The installed-demo scenario: only one month imported, so the previous
  // month has a name and nothing else. 2.4.0 read the name as permission.
  assert.equal(canCompare(uncomparable, "last", false, "2026-05"), false);
});

test("a real previous month is comparable", () => {
  assert.equal(canCompare(comparable, "last", true, "2026-05"), true);
});

test("no previous month name means no comparison either", () => {
  assert.equal(canCompare(comparable, "last", true, ""), false);
});

test("the average baseline is judged on the averages it has", () => {
  assert.equal(canCompare(uncomparable, "average", false, ""), false);
  assert.equal(
    canCompare([{ ...uncomparable[0], average: 40 }], "average", false, ""),
    true,
  );
});

test("an uncomparable month describes no difference", () => {
  for (const item of uncomparable) {
    assert.equal(differenceOf(item, "last", false), null);
  }
});

test("an uncomparable month draws no baseline marker", () => {
  assert.equal(baselineOf(uncomparable[0], "last"), null);
});

test("an uncomparable month shows share only, never higher or lower", () => {
  for (const item of uncomparable) {
    const caption = categoryCaption(
      item, differenceOf(item, "last", false), "the same point last month",
      money,
    );
    assert.equal(caption, `${item.share.toFixed(0)}% of spending`);
    assert.ok(!caption.includes("higher"));
    assert.ok(!caption.includes("below"));
    assert.ok(!caption.includes("in line"));
    assert.ok(!caption.includes("▲"));
    assert.ok(!caption.includes("▼"));
  }
});

test("a comparable month still says higher and lower", () => {
  const [up, down] = comparable;
  assert.equal(
    categoryCaption(up, differenceOf(up, "last", true), "last month", money),
    "▲ $14.20 higher than last month",
  );
  assert.equal(
    categoryCaption(down, differenceOf(down, "last", true), "last month", money),
    "▼ $33.87 below last month",
  );
});

test("a previous month of zero is a real comparison, not a missing one", () => {
  // The distinction the null exists to carry: spending nothing on a category
  // last month is a fact, and it must still compare.
  const item = { amount: 25, share: 10, previous: 0, delta: 25 };
  assert.equal(baselineOf(item, "last"), 0);
  assert.equal(
    categoryCaption(item, differenceOf(item, "last", true), "last month", money),
    "▲ $25.00 higher than last month",
  );
});

test("a difference under a dollar reads as in line", () => {
  const item = { amount: 25, share: 10, previous: 24.6, delta: 0.4 };
  assert.equal(
    categoryCaption(item, differenceOf(item, "last", true), "last month", money),
    "roughly in line with last month",
  );
});
