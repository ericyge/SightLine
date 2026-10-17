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

export const where = (item) => fetch(`/api/where?item=${encodeURIComponent(item)}`).then(json);

/** @param {Blob} frame JPEG */
export function observe(frame, room, ts, sessionId) {
  const body = new FormData();
  body.append("frame", frame, "frame.jpg");
  if (room) body.append("room", room);
  if (ts) body.append("ts", ts);
  if (sessionId) body.append("session_id", sessionId);
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
