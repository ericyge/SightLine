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
