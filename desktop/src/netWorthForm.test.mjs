import assert from "node:assert/strict";
import test from "node:test";

import {
  fieldsFor, formIdentity, parseAmount, parseReading, runningTotal,
} from "./netWorthForm.ts";

const prefill = {
  month: "2026-08", cash: 17400, investments: 48250,
  other_assets: 0, liabilities: 1430,
};
const june = {
  month: "2026-06", cash: 5000, investments: 30000,
  other_assets: 1200, liabilities: 4000, note: "sold the bike",
};
const july = {
  month: "2026-07", cash: 5400, investments: 31000,
  other_assets: 1200, liabilities: 3800, note: "",
};

// ── Edit has to load the reading that was clicked ────────────────────────

test("a new reading starts from the prefill", () => {
  const fields = fieldsFor(undefined, prefill);
  assert.equal(fields.month, "2026-08");
  assert.equal(fields.cash, "17400");
  assert.equal(fields.liabilities, "1430");
  assert.equal(fields.note, "");
});

test("editing loads that month and all four saved components", () => {
  const fields = fieldsFor(june, prefill);
  assert.equal(fields.month, "2026-06");
  assert.equal(fields.cash, "5000");
  assert.equal(fields.investments, "30000");
  assert.equal(fields.otherAssets, "1200");
  assert.equal(fields.liabilities, "4000");
  assert.equal(fields.note, "sold the bike");
});

test("switching from one reading to another changes the identity", () => {
  // The identity is the effect key that reloads the fields. Without it the
  // useState initializer had already run at mount, inside a closed details
  // element, so clicking Edit changed nothing on screen.
  assert.notEqual(formIdentity(june, "2026-08"), formIdentity(july, "2026-08"));
  assert.equal(formIdentity(june, "2026-08"), "edit:2026-06");
});

test("cancelling returns to a clean new-reading identity", () => {
  assert.equal(formIdentity(undefined, "2026-08"), "new:2026-08");
  assert.notEqual(formIdentity(june, "2026-08"), formIdentity(undefined, "2026-08"));
});

test("a parent refresh of the same reading does not change the identity", () => {
  // Same month, refreshed object. An unsaved edit must survive this.
  const refreshed = { ...june };
  assert.equal(formIdentity(june, "2026-08"), formIdentity(refreshed, "2026-08"));
});

// ── a blank field is not a zero ──────────────────────────────────────────

test("a blank amount is refused rather than read as zero", () => {
  const parsed = parseAmount("", "Cash");
  assert.equal(parsed.ok, false);
  assert.match(parsed.reason, /blank/i);
});

test("a blank field cannot silently overwrite a saved balance", () => {
  const fields = fieldsFor(june, prefill);
  const cleared = { ...fields, cash: "" };
  const parsed = parseReading(cleared);
  assert.equal(parsed.ok, false);
  assert.equal(runningTotal(cleared), null);
});

test("whitespace alone is still blank", () => {
  assert.equal(parseAmount("   ", "Cash").ok, false);
});

test("zero itself remains valid", () => {
  const parsed = parseAmount("0", "Cash");
  assert.equal(parsed.ok, true);
  assert.equal(parsed.value, 0);
});

test("an all-zero reading is a legal reading", () => {
  const parsed = parseReading({
    month: "2026-08", cash: "0", investments: "0",
    otherAssets: "0", liabilities: "0", note: "",
  });
  assert.equal(parsed.ok, true);
  assert.equal(parsed.reading.cash, 0);
});

test("a negative component is refused with somewhere to put it", () => {
  const parsed = parseAmount("-5", "Cash");
  assert.equal(parsed.ok, false);
  assert.match(parsed.reason, /Debts/);
});

test("text that is not a number is refused", () => {
  assert.equal(parseAmount("abc", "Cash").ok, false);
  assert.equal(parseAmount("1e999", "Cash").ok, false);
});

// ── the month ────────────────────────────────────────────────────────────

test("a malformed month is refused", () => {
  for (const month of ["", "2026", "2026-13", "2026-00", "2026-01-15", "x"]) {
    const parsed = parseReading({
      month, cash: "1", investments: "0", otherAssets: "0",
      liabilities: "0", note: "",
    });
    assert.equal(parsed.ok, false, `${month} was accepted`);
  }
});

// ── the running total ────────────────────────────────────────────────────

test("the running total is assets minus debts", () => {
  assert.equal(runningTotal(fieldsFor(june, prefill)), 32200);
});

test("the running total can be negative", () => {
  assert.equal(runningTotal({
    month: "2026-08", cash: "100", investments: "0",
    otherAssets: "0", liabilities: "42000", note: "",
  }), -41900);
});

test("the running total keeps cents exact", () => {
  assert.equal(runningTotal({
    month: "2026-08", cash: "0.1", investments: "0.2",
    otherAssets: "0", liabilities: "0", note: "",
  }), 0.3);
});

// The same five rows are asserted against the Python engine in
// tests/test_frontend_does_no_finance_math_2_5_4.py. The preview total and
// the stored net worth must agree, or one of the two suites fails.
test("the preview total matches the engine for the shared fixture rows", () => {
  const rows = [
    [5000, 30000, 12000, 4000, 43000],
    [800, 0, 0, 42000, -41200],
    [0.1, 0.2, 0, 0, 0.3],
    [0, 0, 0, 0, 0],
    [17400, 48250, 0, 1430, 64220],
  ];
  for (const [cash, investments, otherAssets, liabilities, expected] of rows) {
    const total = runningTotal({
      month: "2026-01",
      cash: String(cash), investments: String(investments),
      otherAssets: String(otherAssets), liabilities: String(liabilities),
      note: "",
    });
    assert.equal(total, expected, `${cash}/${investments}/${otherAssets}/${liabilities}`);
  }
});
