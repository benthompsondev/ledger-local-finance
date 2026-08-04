import assert from "node:assert/strict";
import test from "node:test";

import { niceMax } from "./chartScale.ts";

test("niceMax keeps a 2305 peak near the top of the chart", () => {
  assert.equal(niceMax(2305), 2500);
  assert.ok(niceMax(2305) / 2305 < 1.1);
});

test("niceMax uses the finer shared ceiling sequence", () => {
  assert.equal(niceMax(210), 250);
  assert.equal(niceMax(275), 300);
  assert.equal(niceMax(4100), 5000);
});
