"""Vision: the ONLY module that talks to Perceptron. Mock mode (default) needs no key.

Owner: A. Select the implementation with PERCEPTRON_MODE=mock (default) | live.
Never log frames, API keys or full responses.
"""
import logging
import math
import os
import threading
import time
import zlib
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass

log = logging.getLogger("sightline.vision")


class VisionBusy(Exception):
    """A Perceptron call is already in flight; this frame is dropped (the endpoint answers 204)."""


@dataclass
class Detection:
    """One item found in a frame. box is [x1, y1, x2, y2] normalized 0..1."""
    item: str
    box: list[float]
    confidence: float
    spot: str = ""


class VisionClient(ABC):
    """Interface used by /observe and /guide."""
    mock = False
    mode = "unknown"

    @abstractmethod
    def detect(self, frame: bytes, items: list[dict], room: str | None = None,
               hints: dict | None = None) -> list[Detection]:
        """Find the given watchlist items ({name, description, ...}) in one JPEG frame.

        room is the user-selected room (context for the spot string). hints is only used by
        MockVision (forced position/size); real clients ignore it. The live client raises
        VisionBusy when it is already working on another frame, and returns [] ("not seen")
        for any provider error, so callers never crash on a vision problem.
        """

    def stats(self) -> dict | None:
        """Latency numbers for the live client (None for mock). See VisionStats.snapshot()."""
        return None


# ---- mock -------------------------------------------------------------------------------

MOCK_BASE_X = {"wallet": 0.3, "keys": 0.62, "phone": 0.45, "glasses": 0.2, "medication": 0.78}
MOCK_SPOTS = {
    "wallet": "on the desk, next to the lamp",
    "keys": "on the counter, next to the fruit bowl",
    "phone": "on the table, next to the remote",
    "glasses": "on the nightstand, next to the book",
    "medication": "on the shelf, next to the kettle",
}
MOCK_BOX_W = 0.10
MOCK_BOX_H = 0.15
MOCK_CENTER_Y = 0.6


def _triangle(n: int, period: int = 20) -> float:
    """Deterministic wave in [-1, 1]."""
    t = n % period
    half = period / 2
    return t / (half / 2) - 1 if t < half else 3 - t / (half / 2)


class MockVision(VisionClient):
    """Deterministic fake detections: each item sits near a base x and drifts a little per call."""
    mock = True
    mode = "mock"

    def __init__(self) -> None:
        self._calls = 0

    def detect(self, frame: bytes, items: list[dict], room: str | None = None,
               hints: dict | None = None) -> list[Detection]:
        self._calls += 1
        hints = hints or {}
        forced_x, forced_h = hints.get("x"), hints.get("h")
        out = []
        for it in items:
            name = it["name"]
            base = MOCK_BASE_X.get(name, 0.25 + 0.5 * (zlib.crc32(name.encode()) % 100) / 100)
            x = forced_x if forced_x is not None else base + 0.04 * _triangle(self._calls)
            x = min(1.0, max(0.0, x))
            h = forced_h if forced_h is not None else MOCK_BOX_H
            half_w, half_h = MOCK_BOX_W / 2, h / 2
            box = [max(0.0, x - half_w), max(0.0, MOCK_CENTER_Y - half_h),
                   min(1.0, x + half_w), min(1.0, MOCK_CENTER_Y + half_h)]
            spot = MOCK_SPOTS.get(name, "on the table, next to the lamp")
            out.append(Detection(name, [round(v, 3) for v in box], 0.9, spot))
        return out


# ---- real -------------------------------------------------------------------------------

class VisionStats:
    """Thread-safe latency window for Perceptron calls. Numbers only: no frames, no responses."""

    def __init__(self, window: int = 200) -> None:
        self._lock = threading.Lock()
        self._times: deque[float] = deque(maxlen=window)   # seconds, successful calls only
        self.errors = 0
        self.dropped = 0

    def record(self, seconds: float) -> None:
        with self._lock:
            self._times.append(seconds)

    def error(self) -> None:
        with self._lock:
            self.errors += 1

    def drop(self) -> None:
        with self._lock:
            self.dropped += 1

    @staticmethod
    def _percentile(sorted_times: list[float], q: float) -> float:
        """Nearest-rank percentile of a sorted list."""
        return sorted_times[max(0, math.ceil(q * len(sorted_times)) - 1)]

    def snapshot(self) -> dict:
        """{calls, p50_ms, p95_ms, errors, dropped}; the percentiles are None before any success."""
        with self._lock:
            times = sorted(self._times)
            out = {"calls": len(times), "p50_ms": None, "p95_ms": None,
                   "errors": self.errors, "dropped": self.dropped}
        if times:
            out["p50_ms"] = round(self._percentile(times, 0.50) * 1000)
            out["p95_ms"] = round(self._percentile(times, 0.95) * 1000)
        return out


# Model id: PERCEPTRON_MODEL. Only perceptron-mk1 is confirmed by Perceptron's docs. The SDK's own
# registry also lists perceptron-mk1.5 and isaac-0.3-fast, but the gateway decides what a key can use.
DEFAULT_MODEL = "perceptron-mk1"
DEFAULT_TIMEOUT_S = 2.0
# The SDK's BoundingBox has no confidence field, so every live detection gets this placeholder.
UNKNOWN_CONFIDENCE = 0.8
WARN_EVERY_S = 60.0


def _grid(value: float) -> float:
    """One SDK coordinate to 0..1. CONFIRMED in the SDK source (perceptron 0.4.0, pointing/types.py):
    boxes are integers on a normalized 0-1000 grid, independent of the image's pixel size."""
    return min(1.0, max(0.0, value / 1000.0))


def box_from_sdk(box) -> list[float] | None:
    """[x1, y1, x2, y2] in 0..1 from an SDK BoundingBox, or None for a degenerate box."""
    x1, x2 = sorted((_grid(box.top_left.x), _grid(box.bottom_right.x)))
    y1, y2 = sorted((_grid(box.top_left.y), _grid(box.bottom_right.y)))
    if x2 - x1 <= 0 or y2 - y1 <= 0:
        return None
    return [round(v, 4) for v in (x1, y1, x2, y2)]


def match_class(mention: str | None, classes: list[str]) -> int | None:
    """Index of the class a box's mention refers to: exact (case-insensitive), then containment,
    then the only class when there is just one. None when it cannot be matched."""
    m = (mention or "").strip().casefold()
    folded = [c.strip().casefold() for c in classes]
    if m in folded:
        return folded.index(m)
    for i, c in enumerate(folded):
        if m and (m in c or c in m):
            return i
    return 0 if len(classes) == 1 else None


class PerceptronVision(VisionClient):
    """Live client (PERCEPTRON_MODE=live). One SDK `detect` call per frame.

    Per frame: classes = each watched item's description (fall back to its name); all items go
    in ONE call, so observe costs one request per frame. Boxes come back on a 0-1000 grid and are
    converted to 0..1. Only one call runs at a time: a frame that arrives while another is in
    flight raises VisionBusy and is dropped (no queue). Errors, timeouts and 429s return [].

    Confirmed from the SDK source (perceptron 0.4.0): image() takes raw bytes; detect() sets
    expects="box" itself (passing it again raises TypeError); there is no per-call timeout in the
    high-level helpers, so the timeout is the global `configure(timeout=...)`.
    NOT verified: latency, and whether your key can use the chosen model.
    TODO(A): spot text ("on the desk, next to the lamp") needs a second call; it is "" for now.
    TODO(A): in-context example photos per item (detect(examples=...)).
    """
    mode = "live"

    def __init__(self) -> None:
        key = os.getenv("PERCEPTRON_API_KEY", "").strip()
        if not key:
            raise RuntimeError("PERCEPTRON_MODE=live needs PERCEPTRON_API_KEY in .env")
        import perceptron  # imported here so mock mode never needs the SDK
        self.model = os.getenv("PERCEPTRON_MODEL") or DEFAULT_MODEL
        self.timeout_s = float(os.getenv("PERCEPTRON_TIMEOUT_S") or DEFAULT_TIMEOUT_S)
        perceptron.configure(api_key=key, provider="perceptron", timeout=self.timeout_s)
        self._busy = threading.Lock()
        self._stats = VisionStats()
        self._last_warn = -WARN_EVERY_S
        self._failures = 0

    def stats(self) -> dict:
        return self._stats.snapshot()

    def _sdk_boxes(self, frame: bytes, classes: list[str]) -> list:
        """The raw SDK boxes (0-1000 grid) for one JPEG frame."""
        from perceptron import detect, image
        result = detect(image(frame), classes=classes, model=self.model, provider="perceptron",
                        max_outputs=len(classes))
        return result.boxes or []

    def probe(self, frame: bytes, classes: list[str]) -> tuple[list, float]:
        """(raw boxes, seconds) for one call, with errors raised. Used by scripts/perceptron_probe.py."""
        started = time.monotonic()
        boxes = self._sdk_boxes(frame, classes)
        return boxes, time.monotonic() - started

    def _warn(self, error: Exception) -> None:
        """One warning per minute, not per frame. Logs the error class only (never bodies or keys)."""
        self._failures += 1
        now = time.monotonic()
        if now - self._last_warn >= WARN_EVERY_S:
            self._last_warn = now
            log.warning("Perceptron call failed (%s); %d failure(s) so far, treating frames as 'not seen'",
                        type(error).__name__, self._failures)

    def detect(self, frame: bytes, items: list[dict], room: str | None = None,
               hints: dict | None = None) -> list[Detection]:
        if not items:
            return []
        if not self._busy.acquire(blocking=False):
            self._stats.drop()
            raise VisionBusy()
        try:
            classes = [(it.get("description") or it["name"]).strip() or it["name"] for it in items]
            started = time.monotonic()
            try:
                boxes = self._sdk_boxes(frame, classes)
            except Exception as e:  # noqa: BLE001 - timeout, 429, auth, parse, network: all "not seen"
                self._stats.error()
                self._warn(e)
                return []
            self._stats.record(time.monotonic() - started)
            return self._detections(boxes, items, classes)
        finally:
            self._busy.release()

    @staticmethod
    def _detections(boxes: list, items: list[dict], classes: list[str]) -> list[Detection]:
        """Map SDK boxes back to watchlist items; the first usable box per item wins."""
        found: dict[int, Detection] = {}
        for b in boxes:
            idx = match_class(getattr(b, "mention", None), classes)
            box = box_from_sdk(b)
            if idx is None or box is None or idx in found:
                continue
            found[idx] = Detection(items[idx]["name"], box, UNKNOWN_CONFIDENCE, "")
        return [found[i] for i in sorted(found)]


# ---- factory ----------------------------------------------------------------------------

_client: VisionClient | None = None


def get_vision() -> VisionClient:
    """Process-wide vision client chosen by PERCEPTRON_MODE (default: mock; "live" = real Perceptron)."""
    global _client
    if _client is None:
        mode = os.getenv("PERCEPTRON_MODE", "mock").lower()
        _client = PerceptronVision() if mode in ("live", "perceptron") else MockVision()
        if _client.mock:
            log.warning("*** MOCK VISION ACTIVE: detections are fake (PERCEPTRON_MODE=mock) ***")
    return _client


def reset_vision() -> None:
    """Drop the cached client (tests, or after changing env)."""
    global _client
    _client = None
