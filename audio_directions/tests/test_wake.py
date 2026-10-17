"""Adapter tests use fake imports, so neither NumPy nor downloaded models are required."""
import math
import os
import sys
import unittest
from types import ModuleType
from unittest.mock import MagicMock, patch

from audio_directions.config import AudioError, ROOT, VoiceConfig
from audio_directions.wake import MODEL_DIR, WakeDetector


class WakeTests(unittest.TestCase):
    def setUp(self):
        self.numpy = MagicMock()
        self.engine = MagicMock()
        self.model = ModuleType("openwakeword.model")
        self.model.Model = MagicMock(return_value=self.engine)
        self.imports = patch.dict(sys.modules, {"numpy": self.numpy,
                                 "openwakeword": ModuleType("openwakeword"),
                                 "openwakeword.model": self.model})
        self.imports.start()
        self.addCleanup(self.imports.stop)

    @patch("pathlib.Path.is_file", return_value=True)
    def test_keyless_stand_in_and_explicit_onnx_support_paths(self, exists):
        detector = WakeDetector(VoiceConfig())
        kwargs = self.model.Model.call_args.kwargs
        self.assertEqual(kwargs["wakeword_models"], [str(MODEL_DIR / "hey_jarvis_v0.1.onnx")])
        self.assertEqual(kwargs["inference_framework"], "onnx")
        self.assertEqual(kwargs["melspec_model_path"], str(MODEL_DIR / "melspectrogram.onnx"))
        self.assertEqual(kwargs["embedding_model_path"], str(MODEL_DIR / "embedding_model.onnx"))
        self.engine.predict.return_value = {"hey_jarvis_v0.1": .75}
        self.assertEqual(detector.process(bytes(2560)), .75)
        self.numpy.frombuffer.assert_called_once_with(bytes(2560), dtype="<i2")
        detector.reset()
        self.engine.reset.assert_called_once()
        detector.close()
        self.assertIsNone(detector.engine)

    @patch("pathlib.Path.is_file", return_value=True)
    def test_custom_model_score_key(self, exists):
        detector = WakeDetector(VoiceConfig(model_path="models/hey_sightline.onnx"))
        self.engine.predict.return_value = {"hey_sightline": .8}
        self.assertEqual(detector.process(bytes(2560)), .8)
        self.assertEqual(self.model.Model.call_args.kwargs["wakeword_models"],
                         [str(ROOT / "models/hey_sightline.onnx")])

    def test_custom_path_and_stand_in_validation(self):
        for config in (VoiceConfig(model_path="../outside.onnx"),
                       VoiceConfig(model_path="models/other.tflite"),
                       VoiceConfig(keyword="unknown")):
            with self.subTest(config=config), self.assertRaises(AudioError):
                WakeDetector(config)
        self.model.Model.assert_not_called()

    @patch("pathlib.Path.is_file", return_value=False)
    def test_missing_models_fail_without_downloading(self, exists):
        with self.assertRaisesRegex(AudioError, "setup_wake"):
            WakeDetector(VoiceConfig())
        self.model.Model.assert_not_called()

    @patch("pathlib.Path.is_file", return_value=True)
    def test_bad_frame_or_score_fails_closed(self, exists):
        detector = WakeDetector(VoiceConfig())
        with self.assertRaisesRegex(AudioError, "1280 samples"):
            detector.process(bytes(640))
        for score in (math.nan, math.inf, -.1, 1.1):
            self.engine.predict.return_value = {"hey_jarvis_v0.1": score}
            with self.subTest(score=score), self.assertRaises(AudioError):
                detector.process(bytes(2560))

    @patch("audio_directions.config.load_dotenv")
    @patch.dict(os.environ, {"WAKE_THRESHOLD": "0.7", "WAKE_MODEL_PATH": "models/hey_sightline.onnx"}, clear=True)
    def test_env_settings_without_reading_env_file(self, dotenv):
        config = VoiceConfig.load()
        self.assertEqual(config.threshold, .7)
        self.assertEqual(config.model_path, "models/hey_sightline.onnx")


if __name__ == "__main__":
    unittest.main()
