// Frame pacing for the guidance loop. The cap comes from the backend (GUIDE_MAX_FPS in .env, sent on
// GET /api/health) so one setting tunes every client after you measure Perceptron latency.

export const DEFAULT_GUIDE_FPS = 3;
const MIN_FPS = 0.5;
const MAX_FPS = 10;

/** Minimum milliseconds between /api/guide requests for a max frames-per-second cap. */
export function guideIntervalMs(maxFps) {
  const fps = Number.isFinite(maxFps) && maxFps > 0 ? maxFps : DEFAULT_GUIDE_FPS;
  return Math.round(1000 / Math.min(MAX_FPS, Math.max(MIN_FPS, fps)));
}

/** Milliseconds to wait before the next request: the rest of the interval, never negative. */
export const waitMs = (intervalMs, elapsedMs) => Math.max(0, intervalMs - elapsedMs);
