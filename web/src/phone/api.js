// fetch wrappers for the backend. The frontend never talks to Perceptron directly.

async function json(res) {
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export const getHealth = () => fetch("/api/health").then(json);
export const getProfile = () => fetch("/api/profile").then(json);

export const postProfile = (profile) =>
  fetch("/api/profile", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(profile),
  }).then(json);

/** Upload a short WAV for server-side speech-to-text. Resolves {text, provider}. */
export function transcribe(wav) {
  const body = new FormData();
  body.append("audio", wav, "recording.wav");
  return fetch("/api/transcribe", { method: "POST", body }).then(json);
}

const CHAT_TIMEOUT_MS = 4000; // backend gives the model 3 s; this guards a dead server

/** Ask the backend what the user wants. history: last turns as {role, content}. */
export function chat(text, history, currentRoom) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), CHAT_TIMEOUT_MS);
  return fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, history, current_room: currentRoom }),
    signal: ctl.signal,
  }).then(json).finally(() => clearTimeout(timer));
}

export const where = (item) => fetch(`/api/where?item=${encodeURIComponent(item)}`).then(json);

/** @param {Blob} frame JPEG */
export function observe(frame, room, ts) {
  const body = new FormData();
  body.append("frame", frame, "frame.jpg");
  body.append("room", room);
  if (ts) body.append("ts", ts);
  return fetch("/api/observe", { method: "POST", body }).then(json);
}

/** @param {Blob} frame JPEG; yawDeg optional number */
export function guide(frame, item, yawDeg) {
  const body = new FormData();
  body.append("frame", frame, "frame.jpg");
  body.append("item", item);
  if (typeof yawDeg === "number") body.append("yaw_deg", String(yawDeg));
  return fetch("/api/guide", { method: "POST", body }).then(json);
}
