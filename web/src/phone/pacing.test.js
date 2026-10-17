// Run with: npm test
import assert from "node:assert/strict";
import test from "node:test";
import { guideIntervalMs, waitMs } from "./pacing.js";

test("3 fps cap is a 333 ms interval", () => {
  assert.equal(guideIntervalMs(3), 333);
});

test("the cap is clamped to 0.5..10 fps", () => {
  assert.equal(guideIntervalMs(100), 100);
  assert.equal(guideIntervalMs(0.01), 2000);
});

test("missing or invalid caps fall back to 3 fps", () => {
  for (const bad of [undefined, null, NaN, 0, -2, "fast"]) assert.equal(guideIntervalMs(bad), 333);
});

test("waitMs never goes negative: a slow response means the next request goes straight out", () => {
  assert.equal(waitMs(333, 100), 233);
  assert.equal(waitMs(333, 900), 0);
});
