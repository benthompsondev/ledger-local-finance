import assert from "node:assert/strict";
import test from "node:test";

import { niceMax, signedBarGeometry } from "./chartScale.ts";

test("niceMax keeps a 2305 peak near the top of the chart", () => {
  assert.equal(niceMax(2305), 2500);
  assert.ok(niceMax(2305) / 2305 < 1.1);
});

test("niceMax uses the finer shared ceiling sequence", () => {
  assert.equal(niceMax(210), 250);
  assert.equal(niceMax(275), 300);
  assert.equal(niceMax(4100), 5000);
});

test("signed replay geometry shows an improvement that remains negative", () => {
  const actual = signedBarGeometry(0, -500, -500, 0);
  const gain = signedBarGeometry(-500, -300, -500, 0);

  assert.deepEqual(actual, { bottom: 0, height: 100 });
  assert.deepEqual(gain, { bottom: 0, height: 40 });
});

test("signed replay geometry crosses zero instead of collapsing equal magnitudes", () => {
  const actual = signedBarGeometry(0, -100, -100, 100);
  const gain = signedBarGeometry(-100, 100, -100, 100);

  assert.deepEqual(actual, { bottom: 0, height: 50 });
  assert.deepEqual(gain, { bottom: 0, height: 100 });
});
