// Counters for the frame loops (memory and guidance), shown in the app so a silent failure is visible.
// Pure functions, no React or Expo imports, so they can be tested from the command line.

/** Where a frame failed: taking the photo, resizing it, or uploading it to the backend. */
export type Stage = 'camera' | 'resize' | 'upload';

/** An Error that remembers which step of the capture-and-send pipeline failed. */
export class StageError extends Error {
  stage: Stage;

  constructor(stage: Stage, message: string) {
    super(message);
    this.stage = stage;
  }
}

export type Result = {
  kind: 'ok' | 'dropped' | 'failed';   // dropped = HTTP 204, the backend was busy with an earlier frame
  at: number;                          // Date.now() when it finished
  ms: number;                          // how long the whole capture-and-send took
  stage?: Stage;                       // for failures
  error?: string;
};

export type LoopStats = {
  sent: number;      // frames that reached the upload step (ok + dropped + upload failures)
  ok: number;
  dropped: number;
  failed: number;    // any failure, including camera/resize failures where nothing was sent
  last: Result | null;
};

export const emptyStats = (): LoopStats => ({ sent: 0, ok: 0, dropped: 0, failed: 0, last: null });

/** The stats after one more finished attempt. */
export function record(s: LoopStats, r: Result): LoopStats {
  const reachedUpload = r.kind !== 'failed' || r.stage === 'upload';
  return {
    sent: s.sent + (reachedUpload ? 1 : 0),
    ok: s.ok + (r.kind === 'ok' ? 1 : 0),
    dropped: s.dropped + (r.kind === 'dropped' ? 1 : 0),
    failed: s.failed + (r.kind === 'failed' ? 1 : 0),
    last: r,
  };
}

/** A short error message that fits on a phone screen. */
export function errorText(e: unknown): string {
  const raw = e instanceof Error ? `${e.name === 'Error' ? '' : `${e.name}: `}${e.message}` : String(e);
  const oneLine = raw.replace(/\s+/g, ' ').trim();
  return oneLine.length > 90 ? `${oneLine.slice(0, 87)}...` : oneLine || 'unknown error';
}

const ago = (now: number, at: number) => {
  const s = Math.max(0, Math.round((now - at) / 1000));
  return s < 2 ? 'just now' : `${s}s ago`;
};

/** What the last attempt did, in plain words. */
export function describeLast(s: LoopStats, now: number): string {
  const r = s.last;
  if (!r) return 'nothing yet';
  if (r.kind === 'ok') return `OK in ${r.ms} ms, ${ago(now, r.at)}`;
  if (r.kind === 'dropped') return `dropped (backend busy), ${ago(now, r.at)}`;
  return `FAILED at ${r.stage ?? 'upload'}: ${r.error ?? 'unknown error'}, ${ago(now, r.at)}`;
}

/** One debug line: "Memory: 12 sent · 10 ok · 1 dropped · 3 failed". */
export function summarize(label: string, s: LoopStats): string {
  return `${label}: ${s.sent} sent · ${s.ok} ok · ${s.dropped} dropped · ${s.failed} failed`;
}
