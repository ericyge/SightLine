// Guards around taking photos. On iOS expo-camera throws CameraNotReadyException when a previous
// takePictureAsync has not finished, so photos must never overlap, and a stuck one must not block
// everything forever. Pure (no Expo imports) so it can be tested from the command line.

/** Reject if `promise` takes longer than `ms`. The original promise is left running. */
export function withTimeout<T>(promise: Promise<T>, ms: number, message: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(message)), ms);
    promise.then(
      (v) => { clearTimeout(timer); resolve(v); },
      (e) => { clearTimeout(timer); reject(e); },
    );
  });
}

/**
 * Runs one task at a time and refuses (does not queue) a new one while another is unfinished.
 * A task that times out still counts as unfinished until it settles or reset() is called, because
 * the native camera is still busy with it.
 */
export class SingleFlight {
  private busy = false;
  private generation = 0;

  async run<T>(task: () => Promise<T>, timeoutMs: number, timeoutMessage = 'photo timed out'): Promise<T> {
    if (this.busy) throw new Error('previous photo still in progress');
    this.busy = true;
    const mine = this.generation;
    const release = () => { if (this.generation === mine) this.busy = false; };
    let promise: Promise<T>;
    try {
      promise = task();
    } catch (e) {
      release();
      throw e;
    }
    promise.then(release, release);
    return withTimeout(promise, timeoutMs, timeoutMessage);
  }

  /** Forget any unfinished task (use after the camera was restarted). */
  reset() {
    this.generation += 1;
    this.busy = false;
  }

  get inProgress() {
    return this.busy;
  }
}

/**
 * Decides when to restart the camera. A slow photo is NOT a reason: restarting destroys the photo that is
 * about to finish. Restart only when photos keep failing for real, or one has been stuck for a long time.
 */
export class CameraWatchdog {
  private fails = 0;
  private stuckSince: number | null = null;
  private lastRestart = -Infinity;
  private failsBeforeRestart: number;
  private stuckMs: number;
  private cooldownMs: number;

  constructor(failsBeforeRestart: number, stuckMs: number, cooldownMs: number) {
    this.failsBeforeRestart = failsBeforeRestart;
    this.stuckMs = stuckMs;
    this.cooldownMs = cooldownMs;
  }

  /** Report one finished attempt. Returns true when the camera should be restarted now. */
  note(cameraFailed: boolean, error: string | undefined, now: number): boolean {
    if (!cameraFailed) {
      this.fails = 0;
      this.stuckSince = null;
      return false;
    }
    if (error?.includes('still in progress')) {
      this.stuckSince ??= now;            // refused because an earlier photo has not finished
    } else {
      this.fails += 1;                    // a timeout or a real camera error
    }
    const stuck = this.stuckSince !== null && now - this.stuckSince >= this.stuckMs;
    if ((this.fails >= this.failsBeforeRestart || stuck) && now - this.lastRestart >= this.cooldownMs) {
      this.fails = 0;
      this.stuckSince = null;
      this.lastRestart = now;
      return true;
    }
    return false;
  }
}
