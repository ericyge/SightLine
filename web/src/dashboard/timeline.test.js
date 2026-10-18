import test from 'node:test';
import assert from 'node:assert/strict';
import { mergeSightings } from './timeline.js';
const row = (id, second, spot = 'counter') => ({ id, ts: `2026-10-17T20:00:${second}Z`, spot });
test('repeated IDs refresh, newer places stay separate, stale events cannot rewind memory', () => {
  const first = mergeSightings([], [row(1, '01'), row(2, '02')]);
  const next = mergeSightings(first, [row(1, '03')]);
  assert.deepEqual(next.map((s) => s.id), [1, 2]);
  assert.equal(mergeSightings(next, [row(1, '00')])[0].ts, row(1, '03').ts);
  assert.deepEqual(mergeSightings(next, [row(3, '04', 'desk')], 2).map((s) => s.id), [3, 1]);
});
