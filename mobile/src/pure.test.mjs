// Run with: npm test   (Node 22+ runs the .ts files directly; no browser, phone or extra packages)
import assert from 'node:assert/strict';
import test from 'node:test';
import { angleDiff, cameraHeading, compensate, panFromAngle, pitchBoost } from './guidance.ts';
import { guideIntervalMs, waitMs } from './pacing.ts';
import { makeBeepWav } from './wav.ts';

const samples = (wav) => {
  const view = new DataView(wav.buffer, wav.byteOffset, wav.byteLength);
  const frames = (wav.byteLength - 44) / 4;
  let left = 0;
  let right = 0;
  for (let i = 0; i < frames; i++) {
    left = Math.max(left, Math.abs(view.getInt16(44 + i * 4, true)));
    right = Math.max(right, Math.abs(view.getInt16(46 + i * 4, true)));
  }
  return { left, right, view, frames };
};

test('beep WAV header is stereo 16-bit PCM', () => {
  const wav = makeBeepWav(0, 660, 90, 22050);
  const { view, frames } = samples(wav);
  const tag = (o) => String.fromCharCode(...[0, 1, 2, 3].map((i) => view.getUint8(o + i)));
  assert.deepEqual([tag(0), tag(8), tag(12), tag(36)], ['RIFF', 'WAVE', 'fmt ', 'data']);
  assert.equal(view.getUint16(22, true), 2);       // channels
  assert.equal(view.getUint32(24, true), 22050);
  assert.equal(view.getUint16(34, true), 16);
  assert.equal(frames, Math.round(22050 * 0.09));
  assert.equal(view.getUint32(40, true), frames * 4);
});

test('pan -1 plays left only, +1 right only, 0 equally', () => {
  const l = samples(makeBeepWav(-1, 660));
  const r = samples(makeBeepWav(1, 660));
  const c = samples(makeBeepWav(0, 660));
  assert.ok(l.left > 10000 && l.right < 50);
  assert.ok(r.right > 10000 && r.left < 50);
  assert.ok(Math.abs(c.left - c.right) < 50 && c.left > 5000);
});

test('pan is clamped and louder side follows the sign', () => {
  const far = samples(makeBeepWav(5, 660));
  assert.ok(far.right > far.left);
  const slight = samples(makeBeepWav(-0.5, 660));
  assert.ok(slight.left > slight.right && slight.right > 0);
});

test('higher pitch crosses zero more often', () => {
  const crossings = (hz) => {
    const { view, frames } = samples(makeBeepWav(0, hz));
    let n = 0;
    for (let i = 1; i < frames; i++) {
      if (view.getInt16(44 + (i - 1) * 4, true) * view.getInt16(44 + i * 4, true) < 0) n++;
    }
    return n;
  };
  assert.ok(crossings(990) > crossings(660));
});

test('panFromAngle and pitchBoost match the backend rules', () => {
  assert.equal(panFromAngle(0), 0);
  assert.equal(panFromAngle(15), 0.5);
  assert.equal(panFromAngle(90), 1);
  assert.equal(panFromAngle(-90), -1);
  assert.ok(pitchBoost(8) && pitchBoost(-8) && !pitchBoost(8.1));
});

test('compensate follows the backend yaw formula, wraps, and tolerates missing yaw', () => {
  assert.equal(compensate(20, 110, 100), 10);
  assert.equal(compensate(20, 90, 100), 30);
  assert.equal(compensate(20, 5, 355), 10);
  assert.equal(compensate(-5, 355, 5), 5);
  assert.equal(compensate(20, null, 100), 20);
  assert.equal(compensate(20, 100, null), 20);
  assert.equal(angleDiff(10, 350), 20);
});

test('cameraHeading: upright phone facing north is 0, turning left decreases it', () => {
  const rad = (d) => (d * Math.PI) / 180;
  assert.ok(Math.abs(cameraHeading(0, rad(90), 0)) < 1e-9);
  assert.ok(Math.abs(cameraHeading(rad(90), rad(90), 0) - 270) < 1e-6);   // turned left 90 deg
  assert.ok(Math.abs(cameraHeading(rad(-90), rad(90), 0) - 90) < 1e-6);   // turned right 90 deg
});

test('guide pacing: 3 fps default, clamped, never negative', () => {
  assert.equal(guideIntervalMs(3), 333);
  assert.equal(guideIntervalMs(100), 100);
  assert.equal(guideIntervalMs(0.01), 2000);
  for (const bad of [undefined, null, NaN, 0, -2]) assert.equal(guideIntervalMs(bad), 333);
  assert.equal(waitMs(333, 100), 233);
  assert.equal(waitMs(333, 900), 0);
});
