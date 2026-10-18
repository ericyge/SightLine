// Run with: npm test   (Node 22+ runs the .ts files directly; no browser, phone or extra packages)
import assert from 'node:assert/strict';
import test from 'node:test';
import { describeLast, emptyStats, errorText, record, StageError, summarize } from './frameStats.ts';
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
