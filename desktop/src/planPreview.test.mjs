import assert from "node:assert/strict";
import test from "node:test";

import { createRequestGate, previewSignature } from "./planPreview.ts";

const BASE = {
  mode: "normal", income: "5000.00", fixed: "1800.00",
  savings: "800.00", buffer: "200.00",
};

// ── one preview when an unsaved plan loads ───────────────────────────────

test("hydrating with the values already previewed asks nothing more", () => {
  // Plan loads, hydrates the form from a preview, and React then runs the
  // debounced effect because the form changed. Same inputs, same answer:
  // that second request was a whole extra sidecar process for nothing.
  const hydrated = previewSignature(BASE);
  assert.equal(previewSignature({ ...BASE }), hydrated);
});

test("the signature covers every input a preview depends on", () => {
  for (const field of ["mode", "income", "fixed", "savings", "buffer"]) {
    const changed = { ...BASE, [field]: `${BASE[field]}9` };
    assert.notEqual(previewSignature(changed), previewSignature(BASE), field);
  }
});

test("the signature ignores whitespace but not value", () => {
  assert.equal(previewSignature({ ...BASE, income: " 5000.00 " }),
               previewSignature(BASE));
  assert.notEqual(previewSignature({ ...BASE, income: "5000.01" }),
                  previewSignature(BASE));
});

test("missing values do not collapse two different forms together", () => {
  const a = previewSignature({ ...BASE, savings: "", buffer: "200.00" });
  const b = previewSignature({ ...BASE, savings: "200.00", buffer: "" });
  assert.notEqual(a, b);
});

// ── latest request wins ──────────────────────────────────────────────────

test("only the newest request may apply its result", () => {
  const gate = createRequestGate();
  const first = gate.begin();
  const second = gate.begin();

  assert.equal(gate.isLatest(second), true);
  assert.equal(gate.isLatest(first), false);
});

test("responses finishing out of order cannot walk the screen backwards", () => {
  // Typing "1", "12", "123" quickly. The reply for "1" comes back last.
  const gate = createRequestGate();
  const one = gate.begin();
  const two = gate.begin();
  const three = gate.begin();

  const applied = [];
  for (const [token, label] of [[three, "123"], [two, "12"], [one, "1"]]) {
    if (gate.isLatest(token)) applied.push(label);
  }

  assert.deepEqual(applied, ["123"]);
});

test("a stale failure never replaces a newer success", () => {
  // The exact ordering that put an error banner over a correct equation.
  const gate = createRequestGate();
  const slowFailing = gate.begin();
  const fastSucceeding = gate.begin();

  let equation = null;
  let error = "";

  if (gate.isLatest(fastSucceeding)) { equation = "flexible 2200"; error = ""; }
  if (gate.isLatest(slowFailing)) { error = "database is locked"; }

  assert.equal(equation, "flexible 2200");
  assert.equal(error, "", "a stale failure overwrote a newer correct result");
});

test("a success after a failure clears the error", () => {
  const gate = createRequestGate();
  let error = "";

  const failing = gate.begin();
  if (gate.isLatest(failing)) error = "database is locked";
  assert.equal(error, "database is locked");

  const succeeding = gate.begin();
  if (gate.isLatest(succeeding)) error = "";
  assert.equal(error, "", "a working preview left a stale error on screen");
});

test("the newest request still applies when it is the one that failed", () => {
  const gate = createRequestGate();
  gate.begin();
  const newest = gate.begin();

  assert.equal(gate.isLatest(newest), true,
    "a genuine current failure must still be shown");
});

test("a token from before any request is never latest", () => {
  const gate = createRequestGate();
  assert.equal(gate.isLatest(0), false);
  gate.begin();
  assert.equal(gate.isLatest(0), false);
});

test("the gate counts what it issued", () => {
  const gate = createRequestGate();
  assert.equal(gate.issued(), 0);
  gate.begin();
  gate.begin();
  assert.equal(gate.issued(), 2);
});

test("two gates do not share state", () => {
  const a = createRequestGate();
  const b = createRequestGate();
  const first = a.begin();
  b.begin();
  b.begin();

  assert.equal(a.isLatest(first), true, "one screen's requests cancelled another's");
});
