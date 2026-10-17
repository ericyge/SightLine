"""Vision: the ONLY module that talks to Perceptron. Mock mode (default) needs no key.

Owner: A. Select the implementation with PERCEPTRON_MODE=mock|perceptron.
Never log frames or API keys.
"""
import logging
import os
import zlib
from abc import ABC, abstractmethod
from dataclasses import dataclass

log = logging.getLogger("sightline.vision")


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
        MockVision (forced position/size); real clients ignore it.
        """


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

class PerceptronVision(VisionClient):
    """Real client. TODO(A): implement detect().

    Verified from the SDK's PyPI page (perceptron 0.4.0, Python 3.10+):
      * pip install "perceptron>=0.4.0"; key from env PERCEPTRON_API_KEY (or configure(api_key=...)).
      * Models: "perceptron-mk1.5" (default), "isaac-0.3-fast". Pass per call with model=...
        (or env PERCEPTRON_MODEL). Isaac 0.1/0.2 are retired.
      * from perceptron import detect, image
        result = detect(image("frame.jpg"), classes=["wallet", "keys"], model=..., examples=...)
        for box in result.boxes or []: box.mention, box.top_left, box.bottom_right
      * Coordinates are integers on a normalized 0..1000 grid (divide by 1000 for our 0..1).
      * Images must be PNG/JPEG/WebP.
    NOT verified (check the docs / try it before relying on it):
      * Whether image() accepts raw bytes or only a path (else write to a temp file, delete after).
      * Attribute names on top_left / bottom_right points (x, y?).
      * The shape of `examples` for in-context photos of each item.
      * Whether detect() or question(..., expects="box") gives better "spot" text; the spot string
        ("on the desk, next to the lamp") likely needs a second captioning/question call or a
        JSON-schema prompt via response_format.
      * Latency: FIRST TASK is measuring median latency on 20 real 640px frames per model.
    """
    mode = "perceptron"

    def __init__(self) -> None:
        if not os.getenv("PERCEPTRON_API_KEY"):
            raise RuntimeError("PERCEPTRON_MODE=perceptron needs PERCEPTRON_API_KEY in .env")
        self.model = os.getenv("PERCEPTRON_MODEL", "perceptron-mk1.5")

    def detect(self, frame: bytes, items: list[dict], room: str | None = None,
               hints: dict | None = None) -> list[Detection]:
        # TODO(A): call the Perceptron SDK (see class docstring), convert 0..1000 -> 0..1,
        #          map box.mention back to watchlist names, fill spot + confidence.
        # TODO(A): add in-context example photos per item and JSON-schema output.
        raise NotImplementedError("PerceptronVision.detect is not implemented yet (TODO(A))")


# ---- factory ----------------------------------------------------------------------------

_client: VisionClient | None = None


def get_vision() -> VisionClient:
    """Process-wide vision client chosen by PERCEPTRON_MODE (default: mock)."""
    global _client
    if _client is None:
        mode = os.getenv("PERCEPTRON_MODE", "mock").lower()
        _client = PerceptronVision() if mode == "perceptron" else MockVision()
        if _client.mock:
            log.warning("*** MOCK VISION ACTIVE: detections are fake (PERCEPTRON_MODE=mock) ***")
    return _client


def reset_vision() -> None:
    """Drop the cached client (tests, or after changing env)."""
    global _client
    _client = None
