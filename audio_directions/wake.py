"""Local-only openWakeWord adapter. Never downloads models at runtime."""
import math

from .config import AudioError, ROOT

MODEL_DIR = ROOT / "models" / "pretrained"
STAND_INS = {"hey_jarvis": "hey_jarvis_v0.1.onnx"}


class WakeDetector:
    frame_length = 1280  # 80 ms of 16 kHz mono signed int16.

    def __init__(self, config):
        if config.model_path:
            path = (ROOT / config.model_path).resolve()
            if not path.is_relative_to(ROOT / "models") or path.suffix != ".onnx":
                raise AudioError("WAKE_MODEL_PATH must name an .onnx file inside audio_directions/models/.")
        else:
            if config.keyword not in STAND_INS:
                raise AudioError("WAKE_KEYWORD must be hey_jarvis, or set a custom WAKE_MODEL_PATH.")
            path = MODEL_DIR / STAND_INS[config.keyword]
        mel = MODEL_DIR / "melspectrogram.onnx"
        embedding = MODEL_DIR / "embedding_model.onnx"
        for file in (path, mel, embedding):
            if not file.resolve().is_relative_to(ROOT / "models") or not file.is_file():
                raise AudioError(f"Missing/local-only wake model: {file.name}. Run python -m audio_directions.setup_wake; add your custom model if configured.")
        try:
            import numpy as np
            from openwakeword.model import Model
        except ImportError as exc:
            raise AudioError("Install audio_directions/requirements.txt (openwakeword/ONNX dependencies missing).") from exc
        self.np = np
        self.model_name = path.stem
        try:
            self.engine = Model(
                wakeword_models=[str(path)], inference_framework="onnx",
                melspec_model_path=str(mel), embedding_model_path=str(embedding),
                enable_speex_noise_suppression=False, vad_threshold=0,
            )
        except Exception as exc:
            raise AudioError("Cannot load openWakeWord ONNX models. Check downloads, model compatibility, and any .onnx.data sidecar.") from exc

    def process(self, pcm):
        if len(pcm) != self.frame_length * 2:
            raise AudioError("Wake detector requires 1280 samples (80 ms), 16 kHz mono int16 PCM.")
        try:
            samples = self.np.frombuffer(pcm, dtype="<i2").astype(self.np.int16, copy=False)
            predictions = self.engine.predict(samples)
            score = float(predictions[self.model_name])
            if not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError("Invalid model score")
            return score
        except Exception as exc:
            raise AudioError("openWakeWord scoring failed. Expected a compatible single-output wake model.") from exc

    def reset(self):
        self.engine.reset()

    def close(self):
        self.engine = None  # Release ONNX sessions and their buffers.
