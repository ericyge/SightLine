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
