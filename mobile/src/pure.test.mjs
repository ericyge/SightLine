// Run with: npm test   (Node 22+ runs the .ts files directly; no browser, phone or extra packages)
import assert from 'node:assert/strict';
import test from 'node:test';
import { Endpointer, encodeWav16k, levelDb, parseWakeMessage, PcmConverter, streamEncodingFor, toArrayBuffer, wakeUrl } from './wakeCore.ts';
import { CameraWatchdog, SingleFlight, withTimeout } from './capture.ts';
import { describeFirstFailure, describeLast, emptyStats, errorText, record, StageError, summarize } from './frameStats.ts';
import { angleDiff, cameraHeading, compensate, panFromAngle, pitchBoost } from './guidance.ts';
import { guideIntervalMs, waitMs } from './pacing.ts';
import { createVoice, CUE_TIMEOUT_MS, REPLY_TIMEOUT_MS } from './voiceCore.ts';
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

// ---- voice: ElevenLabs reply with built-in fallback ----

function voiceSetup({ tts = 'elevenlabs', ttsGuidance = 'cached', fetchAudio, play } = {}) {
  const log = { fetches: [], played: [], builtin: [] };
  let t = 1000;
  const voice = createVoice({
    fetchAudio: async (text, opts) => { log.fetches.push({ text, ...opts }); return fetchAudio ? fetchAudio(text, opts) : 'audio'; },
    play: async (audio) => { if (play) await play(audio); log.played.push(audio); },
    stopPlayback: () => {},
    builtin: (text, opts) => log.builtin.push({ text, ...opts }),
    getConfig: () => ({ tts, ttsGuidance }),
    now: () => t,
  });
  return { voice, log, tick: (ms) => { t += ms; } };
}

test('voice: reply plays ElevenLabs audio only', async () => {
  const { voice, log } = voiceSetup();
  await voice.reply('Looking for your wallet.');
  assert.deepEqual(log.played, ['audio']);
  assert.deepEqual(log.builtin, []);
  assert.deepEqual(log.fetches, [{ text: 'Looking for your wallet.', cachedOnly: false, timeoutMs: REPLY_TIMEOUT_MS }]);
});

test('voice: TTS off means the built-in voice with no request', async () => {
  const { voice, log } = voiceSetup({ tts: 'browser' });
  await voice.reply('Hello');
  assert.deepEqual(log.fetches, []);
  assert.deepEqual(log.builtin, [{ text: 'Hello', force: true, interrupt: true }]);
});

test('voice: timeout, server error, empty answer and refused playback all fall back', async () => {
  const failures = [
    { fetchAudio: async () => { throw new Error('aborted'); } },
    { fetchAudio: async () => { throw new Error('HTTP 502'); } },
    { fetchAudio: async () => null },
    { play: async () => { throw new Error('cannot play'); } },
  ];
  for (const f of failures) {
    const { voice, log } = voiceSetup(f);
    await voice.reply('Could not find that.');
    assert.deepEqual(log.played, []);
    assert.equal(log.builtin.length, 1, 'the user must always hear something');
  }
});

test('voice: a newer reply wins over a slow older one', async () => {
  let release;
  const slow = new Promise((r) => { release = r; });
  const { voice, log } = voiceSetup({ fetchAudio: (text) => (text === 'old' ? slow.then(() => 'old-audio') : 'new-audio') });
  const first = voice.reply('old');
  await voice.reply('new');
  release();
  await first;
  assert.deepEqual(log.played, ['new-audio']);
  assert.deepEqual(log.builtin, []);
});

test('voice: guidance cues use cached audio only, else stay on the fast built-in voice', async () => {
  let { voice, log } = voiceSetup();
  await voice.cue('Reach out now');
  assert.deepEqual(log.fetches, [{ text: 'Reach out now', cachedOnly: true, timeoutMs: CUE_TIMEOUT_MS }]);
  assert.deepEqual(log.played, ['audio']);
  ({ voice, log } = voiceSetup({ fetchAudio: async () => null }));
  await voice.cue("Wallet at 2 o'clock");
  assert.deepEqual(log.builtin, [{ text: "Wallet at 2 o'clock", interrupt: true }]);
  for (const cfg of [{ ttsGuidance: 'never' }, { tts: 'browser' }]) {
    ({ voice, log } = voiceSetup(cfg));
    await voice.cue('Reach out now');
    assert.deepEqual(log.fetches, []);
    assert.equal(log.builtin.length, 1);
  }
});

test('voice: repeated cues inside four seconds are dropped', async () => {
  const { voice, log, tick } = voiceSetup({ tts: 'browser' });
  await voice.cue('Reach out now');
  tick(1000);
  await voice.cue('Reach out now');
  assert.equal(log.builtin.length, 1);
  tick(4000);
  await voice.cue('Reach out now');
  assert.equal(log.builtin.length, 2);
});

test('voice: stop invalidates a pending reply', async () => {
  let release;
  const slow = new Promise((r) => { release = r; });
  const { voice, log } = voiceSetup({ fetchAudio: () => slow.then(() => 'audio') });
  const pending = voice.reply('Hello');
  voice.stop();
  release();
  await pending;
  assert.deepEqual([log.played, log.builtin], [[], []]);
});

// ---- frame counters shown in the app ----

test('frame stats: counts sent, ok, dropped and failed', () => {
  let s = emptyStats();
  s = record(s, { kind: 'ok', at: 1000, ms: 600 });
  s = record(s, { kind: 'dropped', at: 2000, ms: 200 });
  s = record(s, { kind: 'failed', at: 3000, ms: 50, stage: 'camera', error: 'camera ref is empty' });
  s = record(s, { kind: 'failed', at: 4000, ms: 900, stage: 'resize', error: 'bad image' });
  s = record(s, { kind: 'failed', at: 5000, ms: 8000, stage: 'upload', error: 'Network request failed' });
  // a frame that failed in the camera or resize step was never sent; an upload failure was
  assert.deepEqual({ sent: s.sent, ok: s.ok, dropped: s.dropped, failed: s.failed }, { sent: 3, ok: 1, dropped: 1, failed: 3 });
  assert.equal(summarize('Memory', s), 'Memory: 3 sent · 1 ok · 1 dropped · 3 failed');
});

test('frame stats: the last attempt is described with its failing step', () => {
  assert.equal(describeLast(emptyStats(), 0), 'nothing yet');
  let s = record(emptyStats(), { kind: 'ok', at: 10_000, ms: 640 });
  assert.equal(describeLast(s, 10_500), 'OK in 640 ms, just now');
  assert.equal(describeLast(s, 15_000), 'OK in 640 ms, 5s ago');
  s = record(s, { kind: 'dropped', at: 20_000, ms: 100 });
  assert.equal(describeLast(s, 20_000), 'dropped (backend busy), just now');
  s = record(s, { kind: 'failed', at: 30_000, ms: 10, stage: 'camera', error: 'camera ref is empty' });
  assert.equal(describeLast(s, 33_000), 'FAILED at camera: camera ref is empty, 3s ago');
});

test('frame stats: error text is one short line, and StageError remembers its step', () => {
  assert.equal(errorText(new Error('boom')), 'boom');
  assert.equal(errorText(new TypeError('Network request failed')), 'TypeError: Network request failed');
  assert.equal(errorText(new Error('line one' + String.fromCharCode(10) + '   line two')), 'line one line two');
  assert.ok(errorText(new Error('x'.repeat(300))).length <= 90);
  assert.equal(errorText(''), 'unknown error');
  const e = new StageError('resize', 'bad');
  assert.ok(e instanceof Error && e.stage === 'resize' && e.message === 'bad');
});

// ---- photo guards: iOS refuses a new photo while the previous one is unfinished ----

const never = () => new Promise(() => {});
const later = (ms, value) => new Promise((r) => setTimeout(() => r(value), ms));

test('capture: withTimeout resolves, rejects on error, and rejects when too slow', async () => {
  assert.equal(await withTimeout(later(5, 'ok'), 500, 'slow'), 'ok');
  await assert.rejects(withTimeout(Promise.reject(new Error('boom')), 500, 'slow'), /boom/);
  await assert.rejects(withTimeout(never(), 20, 'photo timed out'), /photo timed out/);
});

test('capture: a second photo is refused (not queued) while the first is unfinished', async () => {
  const flight = new SingleFlight();
  let calls = 0;
  const first = flight.run(() => { calls += 1; return later(40, 'photo'); }, 500);
  await assert.rejects(flight.run(() => { calls += 1; return later(1, 'x'); }, 500), /previous photo still in progress/);
  assert.equal(calls, 1, 'the native camera must not be called while busy');
  assert.equal(await first, 'photo');
  assert.equal(await flight.run(async () => 'next', 500), 'next', 'free again once the first settles');
});

test('capture: a timed-out photo keeps blocking until it settles, so the camera never overlaps', async () => {
  const flight = new SingleFlight();
  let finish;
  const stuck = new Promise((r) => { finish = r; });
  await assert.rejects(flight.run(() => stuck, 20), /photo timed out/);
  assert.equal(flight.inProgress, true);
  await assert.rejects(flight.run(async () => 'x', 500), /still in progress/);
  finish('late');
  await later(5);
  assert.equal(flight.inProgress, false);
  assert.equal(await flight.run(async () => 'ok', 500), 'ok');
});

test('capture: reset() clears a stuck photo after the camera is restarted', async () => {
  const flight = new SingleFlight();
  await assert.rejects(flight.run(never, 20), /timed out/);
  flight.reset();
  assert.equal(await flight.run(async () => 'fresh', 500), 'fresh');
});

test('capture: an error or a synchronous throw frees the guard', async () => {
  const flight = new SingleFlight();
  await assert.rejects(flight.run(() => Promise.reject(new Error('camera error')), 500), /camera error/);
  await later(1);
  assert.equal(flight.inProgress, false);
  await assert.rejects(flight.run(() => { throw new Error('sync'); }, 500), /sync/);
  assert.equal(flight.inProgress, false);
});

test('watchdog: refusals while a photo is merely slow do not restart the camera', () => {
  const w = new CameraWatchdog(3, 20_000, 10_000);
  assert.equal(w.note(true, 'photo timed out', 15_000), false);
  for (let t = 16_000; t < 30_000; t += 1000) assert.equal(w.note(true, 'previous photo still in progress', t), false);
});

test('watchdog: a photo stuck for a long time restarts the camera once, then waits out the cooldown', () => {
  const w = new CameraWatchdog(3, 20_000, 10_000);
  assert.equal(w.note(true, 'previous photo still in progress', 1_000), false);
  assert.equal(w.note(true, 'previous photo still in progress', 20_999), false);
  assert.equal(w.note(true, 'previous photo still in progress', 21_000), true);
  assert.equal(w.note(true, 'previous photo still in progress', 22_000), false);   // cooling down
});

test('watchdog: repeated real camera errors restart, and a success resets everything', () => {
  const w = new CameraWatchdog(3, 20_000, 10_000);
  assert.equal(w.note(true, 'photo timed out', 20_000), false);
  assert.equal(w.note(true, 'photo timed out', 40_000), false);
  assert.equal(w.note(false, undefined, 41_000), false);                           // a good frame resets the count
  assert.equal(w.note(true, 'photo timed out', 60_000), false);
  assert.equal(w.note(true, 'photo timed out', 80_000), false);
  assert.equal(w.note(true, 'photo timed out', 100_000), true);
});

test('frame stats: the first failure is kept even after later, different failures', () => {
  let s = emptyStats();
  assert.equal(describeFirstFailure(s, 0), '');
  s = record(s, { kind: 'ok', at: 1000, ms: 500 });
  s = record(s, { kind: 'failed', at: 5000, ms: 15000, stage: 'camera', error: 'photo timed out' });
  s = record(s, { kind: 'failed', at: 6000, ms: 1, stage: 'camera', error: 'previous photo still in progress' });
  assert.equal(s.last.error, 'previous photo still in progress');
  assert.equal(describeFirstFailure(s, 8000), 'FAILED at camera: photo timed out, 3s ago');
});

test('capture: ageMs reports how long the current photo has been running', async () => {
  const flight = new SingleFlight();
  assert.equal(flight.ageMs(Date.now()), 0);
  const slow = flight.run(() => later(60, 'x'), 1000);
  await later(20);
  assert.ok(flight.ageMs(Date.now()) >= 15);
  await slow;
  assert.equal(flight.ageMs(Date.now()), 0);
});

// ---- "Hey Jarvis": audio conversion, end-of-speech, WAV and URLs ----

const sine = (hz, rate, seconds, amp = 0.5) =>
  Int16Array.from({ length: Math.round(rate * seconds) }, (_, i) => Math.round(Math.sin((2 * Math.PI * hz * i) / rate) * amp * 32767));
const peak = (a) => a.reduce((m, v) => Math.max(m, Math.abs(v)), 0);

test('wake: 16 kHz int16 passes through unchanged (within rounding)', () => {
  const input = sine(440, 16000, 0.1);
  const out = new PcmConverter(16000).push(input.buffer, 1, 'int16');
  assert.equal(out.length, input.length);
  assert.ok(input.every((v, i) => Math.abs(v - out[i]) <= 1));
});

test('wake: 48 kHz is resampled to 16 kHz with the level kept', () => {
  const input = sine(1000, 48000, 1.0);
  const out = new PcmConverter(48000).push(input.buffer, 1, 'int16');
  assert.ok(Math.abs(out.length - 16000) <= 2, `length ${out.length}`);
  assert.ok(Math.abs(peak(out) - peak(input)) < 3000, `peak ${peak(out)} vs ${peak(input)}`);
});

test('wake: uneven chunks give the same audio as one big push', () => {
  const input = sine(700, 44100, 0.5);
  const whole = new PcmConverter(44100).push(input.buffer, 1, 'int16');
  const conv = new PcmConverter(44100);
  const parts = [];
  const sizes = [97, 331, 1000, 53, 777, 1];
  for (let i = 0, k = 0; i < input.length; k++) {
    const piece = input.slice(i, i + sizes[k % sizes.length]);
    parts.push(conv.push(piece.buffer, 1, 'int16'));
    i += piece.length;
  }
  const joined = Int16Array.from(parts.flatMap((p) => [...p]));
  assert.ok(Math.abs(joined.length - whole.length) <= 2);
  const n = Math.min(joined.length, whole.length) - 2;
  assert.ok(joined.slice(0, n).every((v, i) => Math.abs(v - whole[i]) <= 1500), 'chunking must not distort the audio');
});

test('wake: stereo is mixed down and float32 is accepted', () => {
  const stereo = Int16Array.from({ length: 3200 }, (_, i) => (i % 2 === 0 ? 10000 : 20000));
  const mono = new PcmConverter(16000).push(stereo.buffer, 2, 'int16');
  assert.equal(mono.length, 1600);
  assert.ok(mono.every((v) => Math.abs(v - 15000) <= 2));
  const f32 = Float32Array.from({ length: 1600 }, () => 0.5);
  const out = new PcmConverter(16000).push(f32.buffer, 1, 'float32');
  assert.ok(out.every((v) => Math.abs(v - 16384) <= 2));
});

test('wake: levelDb reads silence, half scale and full scale', () => {
  assert.ok(levelDb(new Int16Array(1280)) <= -90);
  assert.ok(Math.abs(levelDb(Int16Array.from({ length: 1280 }, () => 16384)) - -6.02) < 0.1);
  assert.ok(Math.abs(levelDb(Int16Array.from({ length: 1280 }, () => 32767))) < 0.01);
  assert.equal(levelDb(new Int16Array(0)), -120);
});

/** Feed an endpointer 80 ms frames: [seconds, loudness] segments. Returns when it stops (seconds) and why. */
function runEndpointer(segments, opts) {
  const ep = new Endpointer(opts);
  let t = 0;
  for (const [seconds, amp] of segments) {
    for (let i = 0; i < Math.round(seconds / 0.08); i++) {
      t += 0.08;
      const frame = Int16Array.from({ length: 1280 }, (_, k) => Math.round(Math.sin(k) * amp * 32767));
      const r = ep.push(frame);
      if (r.stop) return { at: Math.round(t * 100) / 100, reason: r.reason };
    }
  }
  return { at: null, reason: 'never' };
}

test('wake: stops 1.2 s after the user finishes speaking', () => {
  const r = runEndpointer([[0.8, 0.4], [1.5, 0.3], [3, 0.0001]]);   // wake-word tail, request, then quiet
  assert.equal(r.reason, 'silence');
  assert.ok(r.at > 3.4 && r.at < 3.7, `stopped at ${r.at}`);
});

test('wake: the tail of "Hey Jarvis" itself does not count as speech', () => {
  const r = runEndpointer([[0.6, 0.4], [6, 0.0001]]);               // only the wake word, then nothing
  assert.equal(r.reason, 'no-speech');
  assert.ok(r.at >= 5 && r.at < 5.2, `stopped at ${r.at}`);
});

test('wake: a pause before the request is allowed, and nonstop speech ends at the maximum', () => {
  assert.equal(runEndpointer([[0.7, 0.4], [1.0, 0.0001], [1.0, 0.3], [2, 0.0001]]).reason, 'silence');
  const r = runEndpointer([[20, 0.3]]);
  assert.deepEqual([r.reason, r.at], ['max', 12]);
});

test('wake: the endpointer options can be tuned', () => {
  const r = runEndpointer([[0.2, 0.4], [0.5, 0.3], [2, 0.0001]], { ignoreMs: 100, silenceMs: 400 });
  assert.equal(r.reason, 'silence');
  assert.ok(r.at < 1.5);
});

test('wake: the request is packaged as a valid 16 kHz mono WAV', () => {
  const wav = encodeWav16k(Int16Array.from([0, 1000, -1000, 32767, -32768]));
  const view = new DataView(wav.buffer);
  const tag = (o) => String.fromCharCode(...[0, 1, 2, 3].map((i) => view.getUint8(o + i)));
  assert.deepEqual([tag(0), tag(8), tag(12), tag(36)], ['RIFF', 'WAVE', 'fmt ', 'data']);
  assert.equal(view.getUint32(24, true), 16000);
  assert.equal(view.getUint16(22, true), 1);
  assert.equal(view.getUint32(40, true), 10);
  assert.equal(wav.length, 44 + 10);
  assert.deepEqual([view.getInt16(46, true), view.getInt16(52, true)], [1000, -32768]);
});

test('wake: server address becomes the WebSocket address', () => {
  assert.equal(wakeUrl('https://x-y.trycloudflare.com'), 'wss://x-y.trycloudflare.com/ws/wake');
  assert.equal(wakeUrl('https://x-y.trycloudflare.com/'), 'wss://x-y.trycloudflare.com/ws/wake');
  assert.equal(wakeUrl(' http://172.20.10.6:8000 '), 'ws://172.20.10.6:8000/ws/wake');
  assert.equal(wakeUrl('HTTP://host:1'), 'ws://host:1/ws/wake');
  assert.equal(wakeUrl(''), null);
  assert.equal(wakeUrl('172.20.10.6:8000'), null);
});

test('wake: backend messages are parsed, anything else is ignored', () => {
  assert.deepEqual(parseWakeMessage('{"type":"ready"}'), { type: 'ready' });
  assert.deepEqual(parseWakeMessage('{"type":"wake","score":0.97}'), { type: 'wake', score: 0.97 });
  assert.deepEqual(parseWakeMessage('{"type":"error","reason":"off"}'), { type: 'error', reason: 'off' });
  assert.equal(parseWakeMessage('{"type":"something"}'), null);
  assert.equal(parseWakeMessage('not json'), null);
});

test('wake: the buffer format is decided from the sample rate', () => {
  assert.equal(streamEncodingFor(16000), 'int16');
  assert.equal(streamEncodingFor(16000.0001), 'int16');
  assert.equal(streamEncodingFor(48000), 'float32');
  assert.equal(streamEncodingFor(44100), 'float32');
});

test('wake: stream buffers are unwrapped from whatever the native module returned', () => {
  const ab = new Int16Array([1, 2, 3]).buffer;
  assert.equal(toArrayBuffer(ab), ab);
  const view = new Uint8Array(ab, 2, 4);                       // a window into a larger buffer
  const out = toArrayBuffer(view);
  assert.deepEqual([...new Int16Array(out)], [2, 3]);
  assert.equal(toArrayBuffer('base64text'), null);
  assert.equal(toArrayBuffer(null), null);
});
