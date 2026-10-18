// Browser-side guidance helpers. Mirrors backend/app/guidance.py and web/src/phone/*.js.

export const panFromAngle = (angle: number) => Math.max(-1, Math.min(1, angle / 30));
export const pitchBoost = (angle: number) => Math.abs(angle) <= 8;

/** Shortest signed difference a - b in degrees, in [-180, 180). */
export const angleDiff = (a: number, b: number) => ((((a - b + 180) % 360) + 360) % 360) - 180;

/** Angle to the item after the phone turned since the last vision update. */
export function compensate(lastAngle: number, currentYaw: number | null, yawAtLast: number | null) {
  if (currentYaw == null || yawAtLast == null) return lastAngle;
  return lastAngle - angleDiff(currentYaw, yawAtLast);
}

/**
 * Compass-style heading (degrees, clockwise) of the direction the rear camera faces, from
 * rotation angles in RADIANS (the W3C DeviceOrientation formula, as in web/src/phone/orientation.js).
 * UNVERIFIED on real devices: check the unit expo-sensors reports and the sign while turning.
 */
export function cameraHeading(alpha: number, beta: number, gamma: number) {
  const vx = -Math.cos(alpha) * Math.sin(gamma) - Math.sin(alpha) * Math.sin(beta) * Math.cos(gamma);
  const vy = -Math.sin(alpha) * Math.sin(gamma) + Math.cos(alpha) * Math.sin(beta) * Math.cos(gamma);
  return (((Math.atan2(vx, vy) * 180) / Math.PI) + 360) % 360;
}

export type HandBeepModulation = {
  intervalMs: number;
  pitch: 'low' | 'normal' | 'boost' | 'high';
  direction: 'right_way' | 'wrong_way' | 'steady';
};

/**
 * Modulate beep interval (ms) and pitch from hand/phone motion relative to the target object.
 * - Moving in the right way (angle closing toward 0 or backend beep_ms decreasing) -> higher frequency
 *   (shorter intervalMs + higher pitch).
 * - Moving in the wrong way (angle opening away from 0 or backend beep_ms increasing) -> lower frequency
 *   (longer intervalMs + lower pitch).
 */
export function modulateBeep(
  baseBeepMs: number,
  currentAngle: number,
  prevAngle: number | null,
  prevBaseBeepMs: number | null = null,
  distance: 'far' | 'near' | 'reach' | null = null,
): HandBeepModulation {
  const absNow = Math.abs(currentAngle);
  const absPrev = prevAngle != null ? Math.abs(prevAngle) : absNow;
  const angleDelta = absNow - absPrev;
  const serverDelta = prevBaseBeepMs != null ? baseBeepMs - prevBaseBeepMs : 0;

  const movingRightWay = serverDelta < -8 || angleDelta < -0.6;
  const movingWrongWay = !movingRightWay && (serverDelta > 8 || angleDelta > 0.6);

  // Continuous angle factor: dead-ahead (0 deg) is fastest, off-axis (30+ deg) is slower.
  const aimFactor = 0.8 + 0.55 * Math.min(1, absNow / 30);
  let intervalMs = Math.round(baseBeepMs * aimFactor);

  if (movingRightWay) {
    const speedup = angleDelta < -0.6 ? Math.max(0.55, 0.82 + angleDelta * 0.03) : 0.85;
    intervalMs = Math.max(65, Math.min(260, Math.round(intervalMs * speedup)));
    const pitch = intervalMs <= 145 || distance === 'reach' || absNow <= 6 ? 'high' : 'boost';
    return { intervalMs, pitch, direction: 'right_way' };
  }

  if (movingWrongWay) {
    const slowdown = angleDelta > 0.6 ? Math.min(2.1, 1.32 + angleDelta * 0.04) : 1.35;
    intervalMs = Math.max(300, Math.min(900, Math.round(intervalMs * slowdown)));
    return { intervalMs, pitch: 'low', direction: 'wrong_way' };
  }

  intervalMs = Math.max(70, Math.min(850, intervalMs));
  const pitch = intervalMs <= 140
    ? 'high'
    : (absNow <= 8 || intervalMs <= 220 ? 'boost' : (absNow > 22 ? 'low' : 'normal'));
  return { intervalMs, pitch, direction: 'steady' };
}
