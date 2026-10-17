"""Terminal keys and scoped, interruptible playback; no background input threads."""
import os
import sys
from contextvars import ContextVar

playback_hook = ContextVar("audio_playback_hook", default=None)
playback_start = ContextVar("audio_playback_start", default=None)


class Keys:
    """Nonblocking single keys. Restore terminal settings even on Ctrl+C/errors."""
    def __enter__(self):
        self.saved = None
        self.enabled = sys.stdin.isatty()
        if self.enabled and os.name != "nt":
            import termios
            import tty
            self.saved = termios.tcgetattr(sys.stdin.fileno())
            tty.setcbreak(sys.stdin.fileno())
        return self

    def poll(self):
        if not self.enabled:
            return None
        if os.name == "nt":
            import msvcrt
            key = msvcrt.getwch() if msvcrt.kbhit() else None
        else:
            import select
            key = os.read(sys.stdin.fileno(), 1).decode(errors="ignore") if select.select([sys.stdin], [], [], 0)[0] else None
        if key == "\x03":
            raise KeyboardInterrupt
        return key

    def __exit__(self, *args):
        if self.saved is not None:
            import termios
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self.saved)


def play_pcm(sd, pcm, rate):
    """Return False on interruption. Poll between ~20 ms writes, abort queued audio."""
    hook = playback_hook.get()
    start_hook = playback_start.get()
    if start_hook:
        start_hook()
    with Keys() as keys:
        with sd.RawOutputStream(samplerate=rate, channels=1, dtype="int16", latency="low") as stream:
            chunk = int(rate * .02) * 2
            for start in range(0, len(pcm), chunk):
                if (hook() if hook else keys.poll() is not None):
                    stream.abort()
                    return False
                stream.write(pcm[start:start + chunk])
    return True
