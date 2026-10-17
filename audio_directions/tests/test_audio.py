import io
import os
import unittest
import wave
from unittest.mock import MagicMock, patch

import httpx

from audio_directions.adapter import extract_steps
from audio_directions.config import AudioError, Config, ROOT
from audio_directions.provider import post
from audio_directions.stt import _record_wav, listen_and_transcribe
from audio_directions.tts import speak_directions


class AudioTests(unittest.TestCase):
    def test_adapter_file_and_validation(self):
        self.assertEqual(len(extract_steps(ROOT / "dummy_directions.json")), 3)
        self.assertEqual(extract_steps({"steps": [{"text": " hello "}]}), ["hello"])
        for payload in ({}, {"steps": []}, {"steps": [{"text": 2}]}, {"steps": [None]}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                extract_steps(payload)

    @patch("audio_directions.config.load_dotenv")
    @patch.dict(os.environ, {}, clear=True)
    def test_missing_key(self, dotenv):
        with self.assertRaisesRegex(AudioError, "ELEVENLABS_API_KEY"):
            Config.load()
        dotenv.assert_called_once_with(ROOT / ".env", override=False)

    @patch("audio_directions.provider.httpx.post")
    def test_provider_errors(self, request):
        for code, body, message in (
            (401, {}, "authentication"),
            (403, {}, "authentication"),
            (401, {"detail": {"status": "quota_exceeded"}}, "quota/credits"),
            (429, {}, "limit or quota"),
            (422, {"detail": []}, "HTTP 422"),
        ):
            request.return_value = httpx.Response(code, json=body)
            with self.subTest(code=code, body=body), self.assertRaisesRegex(AudioError, message):
                post("/speech-to-text", Config("secret"))
        request.side_effect = httpx.ReadTimeout("timeout")
        with self.assertRaisesRegex(AudioError, "timed out"):
            post("/speech-to-text", Config("secret"))

    @patch("audio_directions.tts.Config.load", return_value=Config("secret", "voice"))
    @patch("audio_directions.tts.sounddevice")
    @patch("audio_directions.tts.post")
    def test_tts_waits_for_playback_before_next_request(self, request, device, config):
        events = []
        def synthesize(path, config, **kwargs):
            events.append(kwargs["json"]["text"])
            self.assertEqual(kwargs["params"], {"output_format": "pcm_24000"})
            return httpx.Response(200, content=b"\0\0" * 10)
        request.side_effect = synthesize
        stream = device.return_value.RawOutputStream.return_value
        stream.__enter__.return_value.write.side_effect = lambda pcm: events.append("play")
        stream.__exit__.side_effect = lambda *args: events.append("finished")
        self.assertEqual(speak_directions({"steps": [{"text": "one"}, {"text": "two"}]}), [])
        self.assertEqual(events, ["one", "play", "finished", "two", "play", "finished"])

    @patch("audio_directions.tts.Config.load", return_value=Config("secret", "voice"))
    @patch("audio_directions.tts.post")
    def test_reject_outside_output_before_network(self, request, config):
        with self.assertRaisesRegex(ValueError, "inside audio_directions"):
            speak_directions({"steps": [{"text": "one"}]}, play=False, save_dir="../outside")
        request.assert_not_called()

    @patch("audio_directions.stt.sounddevice")
    @patch("builtins.input", return_value="")
    def test_recording_is_valid_in_memory_wav(self, keyboard, device):
        sd = device.return_value
        sd.PortAudioError = RuntimeError
        def create_stream(**kwargs):
            stream = MagicMock()
            stream.__enter__.side_effect = lambda: kwargs["callback"](b"\0\0" * 100, 100, None, None)
            return stream
        sd.RawInputStream.side_effect = create_stream
        with wave.open(io.BytesIO(_record_wav()), "rb") as wav:
            self.assertEqual((wav.getframerate(), wav.getnchannels(), wav.getsampwidth()), (16000, 1, 2))
            self.assertEqual(wav.getnframes(), 100)

    @patch("audio_directions.stt.Config.load", return_value=Config("secret"))
    @patch("audio_directions.stt._record_wav", return_value=b"wav bytes")
    @patch("audio_directions.stt.post")
    def test_stt_upload_and_text_only_result(self, request, record, config):
        request.return_value = httpx.Response(200, json={"text": " Find my keys. ", "words": []})
        self.assertEqual(listen_and_transcribe(), "Find my keys.")
        args, kwargs = request.call_args
        self.assertEqual(args[0], "/speech-to-text")
        self.assertEqual(kwargs["data"]["model_id"], "scribe_v2")
        self.assertEqual(kwargs["files"]["file"], ("recording.wav", b"wav bytes", "audio/wav"))
        request.return_value = httpx.Response(200, json={})
        with self.assertRaisesRegex(AudioError, "invalid transcript"):
            listen_and_transcribe()


if __name__ == "__main__":
    unittest.main()
