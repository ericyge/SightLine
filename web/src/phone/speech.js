// Speech out (SpeechSynthesis) and in (SpeechRecognition). Owner: B.

let lastText = "";
let lastAt = 0;
const REPEAT_MS = 4000; // identical text inside this window is dropped

/** Speak text. Identical repeats within a few seconds are skipped unless force is set. */
export function speak(text, { interrupt = false, force = false } = {}) {
  if (!text || !("speechSynthesis" in window)) return;
  const now = Date.now();
  if (!force && text === lastText && now - lastAt < REPEAT_MS) return;
  lastText = text;
  lastAt = now;
  if (interrupt) window.speechSynthesis.cancel();
  window.speechSynthesis.speak(new SpeechSynthesisUtterance(text));
}

/** Prime speech synthesis from a user gesture (iOS Safari). */
export function unlockSpeech() {
  if ("speechSynthesis" in window) window.speechSynthesis.speak(new SpeechSynthesisUtterance(""));
}

export const recognitionSupported = () =>
  Boolean(window.SpeechRecognition || window.webkitSpeechRecognition);

/**
 * Start one-shot recognition. Returns a stop() function, or null if unsupported.
 * Handlers: onResult(text), onEnd(), onError(err).
 */
export function listen({ onResult, onEnd, onError }) {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    onError?.(new Error("Speech recognition is not supported in this browser"));
    return null;
  }
  const rec = new SR();
  rec.lang = "en-US";
  rec.interimResults = false;
  rec.maxAlternatives = 1;
  rec.onresult = (e) => onResult?.(e.results[0][0].transcript);
  rec.onerror = (e) => onError?.(e);
  rec.onend = () => onEnd?.();
  rec.start();
  return () => rec.stop();
}

const clean = (s) =>
  s.replace(/[?.!,]/g, "").replace(/\b(please|for me)\b/g, "").trim().replace(/^(my|the)\s+/, "");

/**
 * Turn recognized text into a command, or null.
 *   "where's my wallet" -> {type:"where", item:"wallet"}
 *   "guide me to my keys" / "find keys" -> {type:"guide", item:"keys"}
 *   "stop" -> {type:"stop"}
 * TODO(B): handle more phrasings ("have you seen my...", "help me find...").
 */
export function parseCommand(text) {
  const t = text.toLowerCase().trim();
  if (/^(stop|cancel|never ?mind|i got it|got it)\b/.test(t)) return { type: "stop" };
  let m = t.match(/where(?:'s| is| are| did i (?:put|leave))?\s+(.+)/);
  if (m) return { type: "where", item: clean(m[1]) };
  m = t.match(/(?:guide me to|take me to|help me find|find)\s+(.+)/);
  if (m) return { type: "guide", item: clean(m[1]) };
  return null;
}
