// Run with: npm test   (Node 22+ runs the .ts files directly; no browser, phone or extra packages)
import assert from 'node:assert/strict';
import test from 'node:test';
import { Endpointer, encodeWav16k, levelDb, parseWakeMessage, PcmConverter, streamEncodingFor, toArrayBuffer, wakeUrl } from './wakeCore.ts';
import { CameraWatchdog, SingleFlight, withTimeout } from './capture.ts';
import { calibrationCommand, observationFields, observationSession, prepareLearningReply } from './calibration.ts';
import { describeFirstFailure, describeLast, emptyStats, errorText, record, StageError, summarize } from './frameStats.ts';
import { angleDiff, cameraHeading, compensate, modulateBeep, panFromAngle, pitchBoost } from './guidance.ts';
import { guideIntervalMs, waitMs } from './pacing.ts';
import { createVoice, CUE_CACHED_TIMEOUT_MS, CUE_FETCH_TIMEOUT_MS, REPLY_TIMEOUT_MS } from './voiceCore.ts';
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

test('calibration voice commands leave object queries for normal chat', () => {
  assert.equal(calibrationCommand('Start calibration!'), 'start');
  assert.equal(calibrationCommand('map my apartment'), 'start');
  assert.equal(calibrationCommand('Continue mapping'), 'start');
  assert.equal(calibrationCommand('finish calibration'), 'finish');
  assert.equal(calibrationCommand('Stop mapping.'), 'finish');
  assert.equal(calibrationCommand('find my wallet'), null);
  assert.equal(calibrationCommand('stop'), null);
});

test('automatic room uploads omit room and include a valid independent session ID', () => {
  const session = observationSession();
  assert.match(session, /^[a-z0-9][a-z0-9_-]{0,79}$/);
  assert.deepEqual(observationFields('', session), { session_id: session });
  assert.deepEqual(observationFields('kitchen', session), { room: 'kitchen', session_id: session });
});

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

test('modulateBeep: increases frequency when moving the right way and decreases when moving the wrong way', () => {
  const steady = modulateBeep(250, 12, 12, 250, 'near');
  const rightWay = modulateBeep(180, 4, 12, 250, 'reach');
  const wrongWay = modulateBeep(380, 22, 12, 250, 'near');
  assert.equal(rightWay.direction, 'right_way');
  assert.equal(wrongWay.direction, 'wrong_way');
  assert.ok(rightWay.intervalMs < steady.intervalMs);
  assert.ok(wrongWay.intervalMs > steady.intervalMs);
  assert.equal(rightWay.pitch, 'high');
  assert.equal(wrongWay.pitch, 'low');
});

test('guide pacing: 3 fps default, clamped, never negative', () => {
  assert.equal(guideIntervalMs(3), 333);
  assert.equal(guideIntervalMs(100), 100);
  assert.equal(guideIntervalMs(0.01), 2000);
  for (const bad of [undefined, null, NaN, 0, -2]) assert.equal(guideIntervalMs(bad), 333);
  assert.equal(waitMs(333, 100), 233);
  assert.equal(waitMs(333, 900), 0);
});

// ---- voice: one speaker at a time, ElevenLabs voice first, replies never cut off ----

const tick = () => new Promise((r) => setTimeout(r, 0));
const settle = async () => { for (let i = 0; i < 6; i++) await tick(); };

/**
 * A fake phone speaker. Every ElevenLabs/built-in utterance "speaks" until finishAll() (it ended naturally)
 * or stopAll() (it was cut off). `maxConcurrent` is the most voices ever speaking at once.
 */
function voiceRig({ tts = 'elevenlabs', ttsGuidance = 'always', fetchAudio, playFails = false, sleep } = {}) {
  const log = [];
  const speaking = new Map();      // id -> resolve
  let seq = 0;
  let maxConcurrent = 0;
  let t = 1000;
  const begin = (label, resolve) => {
    const id = ++seq;
    speaking.set(id, resolve);
    maxConcurrent = Math.max(maxConcurrent, speaking.size);
    log.push(label);
  };
  const finishAll = () => { for (const [id, resolve] of [...speaking]) { speaking.delete(id); resolve(); } };
  const voice = createVoice({
    fetchAudio: async (text, opts) => {
      log.push(`fetch:${text}${opts.cachedOnly ? ' (cached only)' : ''} ${opts.timeoutMs}ms`);
      return fetchAudio ? fetchAudio(text, opts) : `audio:${text}`;
    },
    play: (audio) => new Promise((resolve, reject) => {
      if (playFails) return reject(new Error('cannot play'));
      return begin(`eleven:${audio.replace('audio:', '')}`, resolve);
    }),
    stopAll: () => { log.push('stopAll'); finishAll(); },
    builtin: (text) => new Promise((resolve) => begin(`builtin:${text}`, resolve)),
    getConfig: () => ({ tts, ttsGuidance }),
    now: () => t,
    ...(sleep ? { sleep } : {}),
  });
  return {
    voice, log, finishAll,
    spoken: () => log.filter((l) => l.startsWith('eleven:') || l.startsWith('builtin:')),
    maxConcurrent: () => maxConcurrent,
    stopCount: () => log.filter((l) => l === 'stopAll').length,
    tick: (ms) => { t += ms; },
  };
}

test('voice: a reply speaks in the ElevenLabs voice only, and finishes when the speech finishes', async () => {
  const r = voiceRig();
  let done = false;
  const p = r.voice.reply('Looking for your wallet.').then(() => { done = true; });
  await settle();
  assert.deepEqual(r.spoken(), ['eleven:Looking for your wallet.']);
  assert.equal(done, false, 'still speaking');
  assert.ok(r.log.includes(`fetch:Looking for your wallet. ${REPLY_TIMEOUT_MS}ms`));
  r.finishAll();
  await p;
  assert.equal(done, true);
});

test('voice: with TTS off the built-in voice is used and nothing is fetched', async () => {
  const r = voiceRig({ tts: 'browser' });
  const p = r.voice.reply('Hello');
  await settle();
  assert.deepEqual(r.spoken(), ['builtin:Hello']);
  assert.ok(!r.log.some((l) => l.startsWith('fetch')));
  r.finishAll();
  await p;
});

for (const [name, opts] of [
  ['a timeout', { fetchAudio: async () => { throw new Error('aborted'); } }],
  ['a server error', { fetchAudio: async () => { throw new Error('HTTP 502'); } }],
  ['an empty answer', { fetchAudio: async () => null }],
  ['audio that cannot start', { playFails: true }],
]) {
  test(`voice: ${name} falls back to the built-in voice, exactly once`, async () => {
    const r = voiceRig(opts);
    const p = r.voice.reply('Could not find that.');
    await settle();
    assert.deepEqual(r.spoken(), ['builtin:Could not find that.'], 'the user must hear something, once');
    r.finishAll();
    await p;
  });
}

test('voice: a newer reply interrupts, and a slow older reply never talks over it', async () => {
  let release;
  const slow = new Promise((res) => { release = res; });
  const r = voiceRig({ fetchAudio: (text) => (text === 'old' ? slow.then(() => 'audio:old') : `audio:${text}`) });
  const first = r.voice.reply('old');
  await settle();
  const second = r.voice.reply('new');
  await settle();
  release();
  await settle();
  assert.deepEqual(r.spoken(), ['eleven:new']);
  r.finishAll();
  await Promise.all([first, second]);
});

test('voice: guidance cues use the ElevenLabs voice too ("always"), falling back to the built-in voice if it fails', async () => {
  let r = voiceRig();
  let p = r.voice.cue("Wallet at 2 o'clock");
  await settle();
  assert.deepEqual(r.spoken(), ["eleven:Wallet at 2 o'clock"]);
  assert.ok(r.log.includes(`fetch:Wallet at 2 o'clock ${CUE_FETCH_TIMEOUT_MS}ms`), 'a real fetch, not cached-only');
  r.finishAll();
  await p;
  r = voiceRig({ fetchAudio: async () => { throw new Error('down'); } });
  p = r.voice.cue('Reach out now');
  await settle();
  assert.deepEqual(r.spoken(), ['builtin:Reach out now']);
  r.finishAll();
  await p;
});

test('voice: "cached" cues use ElevenLabs only for audio the server has; "never" and TTS-off use the built-in voice', async () => {
  let r = voiceRig({ ttsGuidance: 'cached' });
  let p = r.voice.cue('Reach out now');
  await settle();
  assert.ok(r.log.includes(`fetch:Reach out now (cached only) ${CUE_CACHED_TIMEOUT_MS}ms`));
  assert.deepEqual(r.spoken(), ['eleven:Reach out now']);
  r.finishAll();
  await p;
  r = voiceRig({ ttsGuidance: 'cached', fetchAudio: async () => null });
  p = r.voice.cue("Wallet at 2 o'clock");
  await settle();
  assert.deepEqual(r.spoken(), ["builtin:Wallet at 2 o'clock"]);
  r.finishAll();
  await p;
  for (const cfg of [{ ttsGuidance: 'never' }, { tts: 'browser' }]) {
    r = voiceRig(cfg);
    p = r.voice.cue('Reach out now');
    await settle();
    assert.ok(!r.log.some((l) => l.startsWith('fetch')));
    assert.deepEqual(r.spoken(), ['builtin:Reach out now']);
    r.finishAll();
    await p;
  }
});

test('voice: a guidance cue never cuts off a reply; it waits, then speaks', async () => {
  const r = voiceRig();
  const reply = r.voice.reply('Looking for your wallet.');
  await settle();
  const stopsBefore = r.stopCount();
  const cue = r.voice.cue("Wallet at 12 o'clock");
  await settle();
  assert.deepEqual(r.spoken(), ['eleven:Looking for your wallet.'], 'only the reply is speaking');
  assert.equal(r.stopCount(), stopsBefore, 'nothing was silenced');
  r.finishAll();                                   // the reply ends naturally
  await reply;
  await settle();
  assert.deepEqual(r.spoken(), ['eleven:Looking for your wallet.', "eleven:Wallet at 12 o'clock"]);
  r.finishAll();
  await cue;
});

test('voice: of several cues waiting behind a reply, only the newest is spoken', async () => {
  const r = voiceRig();
  const reply = r.voice.reply('Looking for your keys.');
  await settle();
  const cues = ['Keys at 3 o\'clock', 'Keys at 2 o\'clock', 'Keys at 1 o\'clock'].map((c) => r.voice.cue(c));
  await settle();
  r.finishAll();
  await reply;
  await settle();
  assert.deepEqual(r.spoken(), ['eleven:Looking for your keys.', "eleven:Keys at 1 o'clock"]);
  r.finishAll();
  await Promise.all(cues);
});

test('voice: a new reply drops the cues that were waiting, and a newer cue interrupts an older cue', async () => {
  let r = voiceRig();
  const first = r.voice.reply('First answer.');
  await settle();
  const staleCue = r.voice.cue('Stale cue');
  await settle();
  const second = r.voice.reply('Second answer.');
  await settle();
  r.finishAll();
  await Promise.all([first, second, staleCue]);
  await settle();
  assert.ok(!r.spoken().includes('eleven:Stale cue'));
  assert.deepEqual(r.spoken(), ['eleven:First answer.', 'eleven:Second answer.']);

  r = voiceRig();
  const a = r.voice.cue('Keys at 3 o\'clock');
  await settle();
  const b = r.voice.cue('Keys at 2 o\'clock');
  await settle();
  assert.deepEqual(r.spoken(), ["eleven:Keys at 3 o'clock", "eleven:Keys at 2 o'clock"]);
  r.finishAll();
  await Promise.all([a, b]);
});

test('voice: two voices never overlap, whichever engine was speaking', async () => {
  const r = voiceRig({ fetchAudio: async (text) => (text.startsWith('slow') ? null : `audio:${text}`) });
  const a = r.voice.cue('slow cue, spoken by the built-in voice');
  await settle();
  assert.deepEqual(r.spoken(), ['builtin:slow cue, spoken by the built-in voice']);
  const b = r.voice.reply('A reply in the ElevenLabs voice.');       // must silence the built-in voice first
  await settle();
  assert.deepEqual(r.spoken().slice(-1), ['eleven:A reply in the ElevenLabs voice.']);
  const log = r.log;
  assert.ok(log.lastIndexOf('stopAll') < log.indexOf('eleven:A reply in the ElevenLabs voice.'));
  r.finishAll();
  await Promise.all([a, b]);
  assert.equal(r.maxConcurrent(), 1);
});

test('voice: a cue that falls back to the built-in voice silences the ElevenLabs cue that was still speaking', async () => {
  const r = voiceRig({ fetchAudio: async (text) => { if (text.startsWith('bad')) throw new Error('down'); return `audio:${text}`; } });
  const a = r.voice.cue("Keys at 3 o'clock");                       // ElevenLabs, still speaking
  await settle();
  assert.deepEqual(r.spoken(), ["eleven:Keys at 3 o'clock"]);
  const b = r.voice.cue('bad cue falls back');                      // its fetch fails -> built-in voice
  await settle();
  assert.deepEqual(r.spoken(), ["eleven:Keys at 3 o'clock", 'builtin:bad cue falls back']);
  assert.ok(r.log.lastIndexOf('stopAll') < r.log.indexOf('builtin:bad cue falls back'), 'silenced first');
  assert.equal(r.maxConcurrent(), 1, 'never two voices at once');
  r.finishAll();
  await Promise.all([a, b]);
});

test('voice: an identical cue within four seconds is dropped, but a cue that never got spoken can be said later', async () => {
  const r = voiceRig();
  let p = r.voice.cue('Reach out now');
  await settle();
  r.finishAll();
  await p;
  r.tick(1000);
  await r.voice.cue('Reach out now');
  assert.equal(r.spoken().length, 1, 'repeat inside 4 s is dropped');
  r.tick(4000);
  p = r.voice.cue('Reach out now');
  await settle();
  assert.equal(r.spoken().length, 2, 'after 4 s it is spoken again');
  r.finishAll();
  await p;

  const q = voiceRig();
  const reply = q.voice.reply('Looking.');
  await settle();
  const skipped = q.voice.cue('Keys at 3 o\'clock');                // superseded below, so never spoken
  await settle();
  const newest = q.voice.cue('Keys at 2 o\'clock');
  await settle();
  q.finishAll();
  await reply;
  await settle();
  q.finishAll();
  await Promise.all([skipped, newest]);
  const again = q.voice.cue('Keys at 3 o\'clock');                   // it was never spoken, so it is allowed
  await settle();
  assert.ok(q.spoken().includes("eleven:Keys at 3 o'clock"));
  q.finishAll();
  await again;
});

test('voice: stop silences everything and cancels what was waiting', async () => {
  const r = voiceRig();
  const reply = r.voice.reply('Hello there.');
  await settle();
  const cue = r.voice.cue('Keys at 2 o\'clock');
  await settle();
  r.voice.stop();                                   // the user started talking
  await Promise.all([reply, cue]);
  await settle();
  assert.deepEqual(r.spoken(), ['eleven:Hello there.']);
  assert.equal(r.maxConcurrent(), 1);
});

test('voice: a reply that never finishes does not silence guidance forever', async () => {
  const r = voiceRig({ sleep: () => Promise.resolve() });            // the 12 s wait elapses at once
  void r.voice.reply('A reply that gets stuck.');
  await settle();
  const cue = r.voice.cue('Keys at 2 o\'clock');
  await settle();
  assert.ok(r.spoken().includes("eleven:Keys at 2 o'clock"));
  r.finishAll();
  await cue;
});

test('voice: empty text is ignored', async () => {
  const r = voiceRig();
  await r.voice.reply('');
  await r.voice.cue('');
  assert.deepEqual(r.log, []);
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

test('background learning settles its frame before recall and continues for unknown items', async () => {
  const calls = [];
  let done;
  const pending = new Promise((resolve) => { done = resolve; });
  const reply = prepareLearningReply(pending,
    async () => { calls.push('chat'); return { intent: 'where' }; },
    async () => { calls.push('finish'); return true; });
  assert.deepEqual(calls, []);
  done();
  assert.equal((await reply).intent, 'where');
  assert.deepEqual(calls, ['chat']);
});

test('background learning is saved before navigation and retained on non-navigation requests', async () => {
  for (const intent of ['find', 'where', 'stop', 'chitchat']) {
    const calls = [];
    const reply = await prepareLearningReply(null,
      async () => { calls.push('chat'); return { intent }; },
      async () => { calls.push('finish'); return true; });
    calls.push(reply.intent);
    assert.deepEqual(calls, intent === 'find' ? ['chat', 'finish', intent] : ['chat', intent]);
  }
});

test('failed save cannot launch navigation; a failed frame still permits honest recall', async () => {
  await assert.rejects(prepareLearningReply(null, async () => ({ intent: 'find' }), async () => false));
  const reply = await prepareLearningReply(Promise.reject(new Error('frame failed')),
    async () => ({ intent: 'where' }), async () => { throw new Error('must not finish'); });
  assert.equal(reply.intent, 'where');
});
