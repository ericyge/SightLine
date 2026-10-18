"""Wake word ("Hey Jarvis") for the phone apps: WS /ws/wake.

The phone streams 16 kHz mono int16 microphone audio while the app is open; this runs the local
openWakeWord model (the same stand-in model audio_directions uses) and answers {"type": "wake"} when it
hears the phrase. Opt-in: WAKE_ENABLED=true plus the optional packages (requirements-wake.txt) and the
models (python -m audio_directions.setup_wake). Models are never downloaded at runtime.

Privacy: audio is scored in memory frame by frame and discarded. It is never stored, forwarded or logged;
only the wake event leaves this module. Never log audio or scores per frame.
"""
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger("sightline.wake")

FRAME_SAMPLES = 1280            # 80 ms at 16 kHz, what openWakeWord scores per step
FRAME_BYTES = FRAME_SAMPLES * 2
MAX_CHUNK_BYTES = 64_000        # larger messages are rejected (2 s of audio is 64 KB)
MAX_SESSIONS = 2
DEFAULT_THRESHOLD = 0.6         # same default as audio_directions
COOLDOWN_S = 3.0                # ignore further wakes right after one, so one "Hey Jarvis" fires once
KEYWORD_FILE = "hey_jarvis_v0.1.onnx"
MODEL_NAME = "hey_jarvis_v0.1"
SUPPORT_FILES = ("melspectrogram.onnx", "embedding_model.onnx")


class WakeUnavailable(RuntimeError):
    """Wake listening cannot run; the message says why and what to do."""


def enabled() -> bool:
    return os.getenv("WAKE_ENABLED", "false").lower() in ("1", "true", "yes", "on")


def threshold() -> float:
    try:
        value = float(os.getenv("WAKE_THRESHOLD") or DEFAULT_THRESHOLD)
    except ValueError:
        return DEFAULT_THRESHOLD
    return value if 0 < value <= 1 else DEFAULT_THRESHOLD


def model_dir() -> Path:
    default = Path(__file__).resolve().parents[2] / "audio_directions" / "models" / "pretrained"
    return Path(os.getenv("WAKE_MODEL_DIR") or default)


def availability() -> tuple[bool, str]:
    """(ok, reason). Cheap checks only: the packages import and the model files exist."""
    if not enabled():
        return False, "Wake word is off. Set WAKE_ENABLED=true in .env and restart the backend."
    try:
        import numpy  # noqa: F401
        import openwakeword  # noqa: F401
    except ImportError:
        return False, "Wake packages are missing. Run: pip install -r requirements-wake.txt"
    missing = [f for f in (KEYWORD_FILE, *SUPPORT_FILES) if not (model_dir() / f).is_file()]
    if missing:
        return False, f"Wake models are missing ({', '.join(missing)}). Run: python -m audio_directions.setup_wake"
    return True, ""


class OpenWakeWordEngine:
    """The real detector: scores one 80 ms int16 frame at a time. One instance per connection."""

    def __init__(self) -> None:
        import numpy as np
        from openwakeword.model import Model
        self._np = np
        folder = model_dir()
        self._engine = Model(
            wakeword_models=[str(folder / KEYWORD_FILE)], inference_framework="onnx",
            melspec_model_path=str(folder / SUPPORT_FILES[0]),
            embedding_model_path=str(folder / SUPPORT_FILES[1]),
            enable_speex_noise_suppression=False, vad_threshold=0,
        )

    def score(self, frame: bytes) -> float:
        samples = self._np.frombuffer(frame, dtype="<i2").astype(self._np.int16, copy=False)
        return float(self._engine.predict(samples)[MODEL_NAME])

    def reset(self) -> None:
        self._engine.reset()


class WakeSession:
    """Chunks of PCM in, wake events out. Holds the partial frame between chunks."""

    def __init__(self, engine, limit: float, cooldown_s: float = COOLDOWN_S) -> None:
        self._engine = engine
        self._limit = limit
        self._cooldown_s = cooldown_s
        self._pending = b""
        self._quiet_until = float("-inf")

    def process(self, chunk: bytes, now: float) -> dict | None:
        """Feed audio (any length, even number of bytes). Returns {"type": "wake", "score": s} or None."""
        if len(chunk) % 2:
            raise ValueError("audio must be 16-bit samples (an even number of bytes)")
        data = self._pending + chunk
        usable = len(data) - len(data) % FRAME_BYTES
        self._pending = data[usable:]
        best = 0.0
        for start in range(0, usable, FRAME_BYTES):
            best = max(best, self._engine.score(data[start:start + FRAME_BYTES]))
        if best >= self._limit and now >= self._quiet_until:
            self._quiet_until = now + self._cooldown_s
            self._engine.reset()
            self._pending = b""
            return {"type": "wake", "score": round(best, 3)}
        return None

    def reset(self) -> None:
        """Forget buffered audio and detector state (the phone does this before it resumes streaming)."""
        self._pending = b""
        self._engine.reset()


_lock = threading.Lock()
_open_sessions = 0


def new_session() -> WakeSession:
    """A session with a fresh model. Raises WakeUnavailable (with the reason) or when too many are open."""
    ok, reason = availability()
    if not ok:
        raise WakeUnavailable(reason)
    try:
        engine = OpenWakeWordEngine()
    except Exception as e:  # noqa: BLE001 - a broken model file must not crash the server
        raise WakeUnavailable(f"Could not load the wake model ({type(e).__name__})") from None
    return WakeSession(engine, threshold())


def acquire_slot() -> bool:
    """Reserve one of MAX_SESSIONS connection slots."""
    global _open_sessions
    with _lock:
        if _open_sessions >= MAX_SESSIONS:
            return False
        _open_sessions += 1
        return True


def release_slot() -> None:
    global _open_sessions
    with _lock:
        _open_sessions = max(0, _open_sessions - 1)
