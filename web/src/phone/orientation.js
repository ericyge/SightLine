// Gyroscope yaw tracking + smoothing between vision updates. Owner: B.
// Yaw is clockwise-positive degrees (turn right => yaw increases), matching the angle sign.
// UNVERIFIED on real devices: the heading math follows the W3C DeviceOrientation example for the
// direction the BACK camera points; check sign and drift on a real iPhone and Android phone.

const SMOOTHING = 0.3; // exponential smoothing weight for the newest reading
let yaw = null;
let handler = null;

/** Shortest signed difference a - b in degrees, in [-180, 180). */
export const angleDiff = (a, b) => ((((a - b + 180) % 360) + 360) % 360) - 180;

/** Browser mirror of backend guidance.yaw_compensate; returns lastAngle if yaw is unknown. */
export function compensate(lastAngle, currentYaw, yawAtLast) {
  if (currentYaw == null || yawAtLast == null) return lastAngle;
  return lastAngle - angleDiff(currentYaw, yawAtLast);
}

/** Compass-style heading (deg, clockwise from north) of the direction the rear camera faces. */
export function cameraHeading(alpha, beta, gamma) {
  const r = Math.PI / 180;
  const [z, x, y] = [alpha * r, beta * r, gamma * r];
  const vx = -Math.cos(z) * Math.sin(y) - Math.sin(z) * Math.sin(x) * Math.cos(y);
  const vy = -Math.sin(z) * Math.sin(y) + Math.cos(z) * Math.sin(x) * Math.cos(y);
  const deg = (Math.atan2(vx, vy) * 180) / Math.PI;
  return (deg + 360) % 360;
}

/** Ask permission (required on iOS 13+; must be called from a user gesture). */
export async function requestOrientationPermission() {
  const D = window.DeviceOrientationEvent;
  if (!D) return false;
  if (typeof D.requestPermission === "function") {
    try { return (await D.requestPermission()) === "granted"; } catch { return false; }
  }
  return true;
}

/** Begin listening. Safe to call repeatedly. */
export function startOrientation() {
  if (handler || !window.DeviceOrientationEvent) return;
  handler = (e) => {
    if (e.alpha == null || e.beta == null || e.gamma == null) return;
    const h = cameraHeading(e.alpha, e.beta, e.gamma);
    yaw = yaw == null ? h : (yaw + SMOOTHING * angleDiff(h, yaw) + 360) % 360;
  };
  window.addEventListener("deviceorientation", handler);
}

export function stopOrientation() {
  if (handler) window.removeEventListener("deviceorientation", handler);
  handler = null;
  yaw = null;
}

/** Smoothed yaw in degrees, or null when no sensor data (desktop, permission denied). */
export const getYaw = () => yaw;
