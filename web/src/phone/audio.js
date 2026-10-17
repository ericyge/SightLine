// Beep engine: Web Audio + StereoPannerNode. Owner: B.
// Params (pan, interval, pitch) are read live, so the caller can update them between server
// responses (e.g. from gyroscope-compensated angles).

let ctx = null;

/** Create/resume the AudioContext. Call from a user gesture (iOS requires it). */
export function unlockAudio() {
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return null;
  if (!ctx) ctx = new AC();
  if (ctx.state === "suspended") ctx.resume();
  return ctx;
}

/** Same rules as backend/app/guidance.py. */
export const panFromAngle = (angle) => Math.max(-1, Math.min(1, angle / 30));
export const pitchBoost = (angle) => Math.abs(angle) <= 8;

const BASE_HZ = 660;
const BOOST_HZ = 990;
const BEEP_SEC = 0.09;

/** Play one short beep panned left/right. pan in -1..1. */
export function beep({ pan = 0, boost = false } = {}) {
  const c = unlockAudio();
  if (!c) return;
  const osc = c.createOscillator();
  const gain = c.createGain();
  const panner = c.createStereoPanner ? c.createStereoPanner() : null;
  osc.frequency.value = boost ? BOOST_HZ : BASE_HZ;
  const t = c.currentTime;
  gain.gain.setValueAtTime(0.0001, t);
  gain.gain.exponentialRampToValueAtTime(0.5, t + 0.01);
  gain.gain.exponentialRampToValueAtTime(0.0001, t + BEEP_SEC);
  osc.connect(gain);
  if (panner) {
    panner.pan.value = pan;
    gain.connect(panner).connect(c.destination);
  } else {
    gain.connect(c.destination);
  }
  osc.start(t);
  osc.stop(t + BEEP_SEC + 0.02);
}

/**
 * Repeating beeper. update({pan, intervalMs, boost}); intervalMs <= 0 means silent.
 * TODO(B): tune pitch/volume curves; consider a continuous tone for "reach".
 */
export function createBeeper() {
  let params = { pan: 0, intervalMs: 0, boost: false };
  let timer = null;
  let running = false;

  function tick() {
    if (!running) return;
    if (params.intervalMs > 0) beep(params);
    timer = setTimeout(tick, params.intervalMs > 0 ? params.intervalMs : 100);
  }

  return {
    update(p) { params = { ...params, ...p }; },
    start() { if (!running) { running = true; tick(); } },
    stop() { running = false; clearTimeout(timer); },
  };
}
