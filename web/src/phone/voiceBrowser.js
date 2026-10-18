// Browser wiring for voice.js: fetch mp3 from /api/speak, play it, fall back to speechSynthesis.
// Only the phone page imports this; the dashboard must stay silent.
import { speak } from "./speech.js";
import { createVoice } from "./voice.js";

let config = { tts: "browser", ttsGuidance: "cached" };

/** Apply the backend's GET /api/health fields {tts, tts_guidance}. */
export function setVoiceConfig(health) {
  config = { tts: health?.tts ?? "browser", ttsGuidance: health?.tts_guidance ?? "cached" };
}

// One reusable element: once it has played inside a user gesture, later play() calls are allowed on iOS.
// UNVERIFIED on real devices.
let el = null;
let currentUrl = null;
const SILENT_WAV = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=";

function element() {
  if (!el) el = new Audio();
  return el;
}

function release() {
  if (currentUrl) URL.revokeObjectURL(currentUrl);
  currentUrl = null;
}

/** Call from a user gesture (the talk button) so later audio playback is allowed. */
export function unlockVoice() {
  const a = element();
  if (!a.paused || a.currentSrc) return;
  a.src = SILENT_WAV;
  a.play().catch(() => {});
}

async function fetchAudio(text, { cachedOnly, timeoutMs }) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const res = await fetch(`/api/speak${cachedOnly ? "?cached_only=true" : ""}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
      signal: ctl.signal,
    });
    if (res.status === 204) return null;            // not cached
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.blob();
  } finally {
    clearTimeout(timer);
  }
}

function stopPlayback() {
  if (el) { el.pause(); el.removeAttribute("src"); el.load?.(); }
  release();
}

function play(blob) {
  const a = element();
  release();
  currentUrl = URL.createObjectURL(blob);
  a.src = currentUrl;
  a.onended = release;
  return a.play();                                    // rejects if the browser blocks it: caller falls back
}

export const voice = createVoice({
  fetchAudio, play, stopPlayback, builtin: speak, getConfig: () => config,
});
