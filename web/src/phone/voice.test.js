// Run with: npm test
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { createVoice, CUE_TIMEOUT_MS, REPLY_TIMEOUT_MS } from "./voice.js";

/** A voice with fake dependencies that record what happened. */
function setup({ tts = "elevenlabs", ttsGuidance = "cached", fetchAudio, play } = {}) {
  const log = { fetches: [], played: [], builtin: [], stops: 0 };
  let t = 1000;
  const voice = createVoice({
    fetchAudio: async (text, opts) => { log.fetches.push({ text, ...opts }); return fetchAudio ? fetchAudio(text, opts) : "audio"; },
    play: async (audio) => { if (play) await play(audio); log.played.push(audio); },
    stopPlayback: () => { log.stops += 1; },
    builtin: (text, opts) => log.builtin.push({ text, ...opts }),
    getConfig: () => ({ tts, ttsGuidance }),
    now: () => t,
  });
  return { voice, log, tick: (ms) => { t += ms; } };
}

test("reply plays the ElevenLabs audio and does not also use the built-in voice", async () => {
  const { voice, log } = setup();
  await voice.reply("Looking for your wallet.");
  assert.deepEqual(log.played, ["audio"]);
  assert.deepEqual(log.builtin, []);
  assert.deepEqual(log.fetches, [{ text: "Looking for your wallet.", cachedOnly: false, timeoutMs: REPLY_TIMEOUT_MS }]);
});

test("reply uses the built-in voice when TTS is off, without any request", async () => {
  const { voice, log } = setup({ tts: "browser" });
  await voice.reply("Hello");
  assert.deepEqual(log.fetches, []);
  assert.deepEqual(log.builtin, [{ text: "Hello", force: true, interrupt: true }]);
});

for (const [name, fetchAudio] of [
  ["a timeout", async () => { throw new Error("aborted"); }],
  ["a server error (401/429/502)", async () => { throw new Error("HTTP 502"); }],
  ["an empty answer", async () => null],
]) {
  test(`reply falls back to the built-in voice on ${name}`, async () => {
    const { voice, log } = setup({ fetchAudio });
    await voice.reply("Could not find that.");
    assert.deepEqual(log.played, []);
    assert.deepEqual(log.builtin, [{ text: "Could not find that.", force: true, interrupt: true }]);
  });
}

test("reply falls back when the browser refuses to play the audio", async () => {
  const { voice, log } = setup({ play: async () => { throw new Error("NotAllowedError"); } });
  await voice.reply("Hello");
  assert.deepEqual(log.played, []);
  assert.equal(log.builtin.length, 1);
});

test("a newer reply wins: a slow older one never talks over it", async () => {
  let release;
  const slow = new Promise((r) => { release = r; });
  const { voice, log } = setup({ fetchAudio: (text) => (text === "old" ? slow.then(() => "old-audio") : "new-audio") });
  const first = voice.reply("old");
  await voice.reply("new");
  release();
  await first;
  assert.deepEqual(log.played, ["new-audio"]);
  assert.deepEqual(log.builtin, []);
});

test("a failure of a superseded reply does not trigger a stray built-in voice", async () => {
  let fail;
  const slow = new Promise((_, rej) => { fail = rej; });
  const { voice, log } = setup({ fetchAudio: (text) => (text === "old" ? slow : "new-audio") });
  const first = voice.reply("old");
  await voice.reply("new");
  fail(new Error("late"));
  await first;
  assert.deepEqual(log.builtin, []);
});

test("cue uses cached ElevenLabs audio when it exists", async () => {
  const { voice, log } = setup();
  await voice.cue("Reach out now");
  assert.deepEqual(log.fetches, [{ text: "Reach out now", cachedOnly: true, timeoutMs: CUE_TIMEOUT_MS }]);
  assert.deepEqual(log.played, ["audio"]);
  assert.deepEqual(log.builtin, []);
});

test("cue uses the built-in voice when the audio is not cached, so guidance stays fast", async () => {
  const { voice, log } = setup({ fetchAudio: async () => null });
  await voice.cue("Wallet at 2 o'clock");
  assert.deepEqual(log.played, []);
  assert.deepEqual(log.builtin, [{ text: "Wallet at 2 o'clock", interrupt: true }]);
});

test("cue never asks the server when guidance is set to never, or TTS is off", async () => {
  for (const cfg of [{ ttsGuidance: "never" }, { tts: "browser" }]) {
    const { voice, log } = setup(cfg);
    await voice.cue("Reach out now");
    assert.deepEqual(log.fetches, []);
    assert.equal(log.builtin.length, 1);
  }
});

test("cue falls back on any error", async () => {
  const { voice, log } = setup({ fetchAudio: async () => { throw new Error("down"); } });
  await voice.cue("Reach out now");
  assert.equal(log.builtin.length, 1);
});

test("identical guidance cues inside four seconds are dropped, later ones are spoken", async () => {
  const { voice, log, tick } = setup({ tts: "browser" });
  await voice.cue("Wallet at 2 o'clock");
  tick(1000);
  await voice.cue("Wallet at 2 o'clock");
  assert.equal(log.builtin.length, 1);
  tick(4000);
  await voice.cue("Wallet at 2 o'clock");
  assert.equal(log.builtin.length, 2);
});

test("stop halts playback and invalidates a pending reply", async () => {
  let release;
  const slow = new Promise((r) => { release = r; });
  const { voice, log } = setup({ fetchAudio: () => slow.then(() => "audio") });
  const pending = voice.reply("Hello");
  voice.stop();                                     // the user started talking
  release();
  await pending;
  assert.deepEqual(log.played, []);
  assert.deepEqual(log.builtin, []);
});

test("empty text is ignored", async () => {
  const { voice, log } = setup();
  await voice.reply("");
  await voice.cue("");
  assert.deepEqual([log.fetches, log.played, log.builtin], [[], [], []]);
});

test("only the phone speaks: the dashboard does not use any audio or voice code", () => {
  const src = readFileSync(new URL("../dashboard/Dashboard.jsx", import.meta.url), "utf8");
  for (const banned of ["speechSynthesis", "new Audio", "voice", "speech.js", "audio.js", "AudioContext", "/api/speak"]) {
    assert.ok(!src.includes(banned), `Dashboard.jsx must not contain ${banned}`);
  }
});

test('cue treats the new "always" setting like "cached" on the web page (cached-only lookups)', async () => {
  const { voice, log } = setup({ ttsGuidance: "always" });
  await voice.cue("Reach out now");
  assert.deepEqual(log.fetches, [{ text: "Reach out now", cachedOnly: true, timeoutMs: CUE_TIMEOUT_MS }]);
});
