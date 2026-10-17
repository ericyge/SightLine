// Run with: npm test   (node's built-in runner; no browser or extra packages)
import assert from "node:assert/strict";
import test from "node:test";
import { downsample, encodeWav } from "./recorder.js";

test("encodeWav writes a valid 16 kHz mono PCM header", () => {
  const view = new DataView(encodeWav(new Float32Array(160), 16000));
  const tag = (o) => String.fromCharCode(...[0, 1, 2, 3].map((i) => view.getUint8(o + i)));
  assert.equal(tag(0), "RIFF");
  assert.equal(tag(8), "WAVE");
  assert.equal(tag(12), "fmt ");
  assert.equal(tag(36), "data");
  assert.equal(view.getUint32(4, true), 36 + 320);
  assert.equal(view.getUint16(20, true), 1);       // PCM
  assert.equal(view.getUint16(22, true), 1);       // mono
  assert.equal(view.getUint32(24, true), 16000);
  assert.equal(view.getUint32(28, true), 32000);   // byte rate
  assert.equal(view.getUint16(34, true), 16);
  assert.equal(view.getUint32(40, true), 320);     // data bytes
  assert.equal(view.byteLength, 44 + 320);
});

test("encodeWav converts and clamps samples", () => {
  const view = new DataView(encodeWav(Float32Array.from([0, 1, -1, 2, -2, 0.5]), 16000));
  const at = (i) => view.getInt16(44 + i * 2, true);
  assert.deepEqual([0, 1, 2, 3, 4].map(at), [0, 32767, -32768, 32767, -32768]);
  assert.equal(at(5), Math.trunc(0.5 * 0x7fff));
});

test("downsample 48 kHz to 16 kHz averages groups of three", () => {
  const out = downsample(Float32Array.from([1, 2, 3, 4, 5, 6, 7]), 48000, 16000);
  assert.equal(out.length, 2);
  assert.deepEqual([...out], [2, 5]);
});

test("downsample leaves equal or lower rates alone", () => {
  const input = Float32Array.from([1, 2, 3]);
  assert.equal(downsample(input, 16000, 16000), input);
  assert.equal(downsample(input, 8000, 16000), input);
});

test("downsample length matches duration for 44.1 kHz", () => {
  const out = downsample(new Float32Array(44100), 44100, 16000);
  assert.ok(Math.abs(out.length - 16000) <= 1);
});
