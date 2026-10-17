import io
import math
import random
import struct
import unittest
import wave
from collections import deque
from dataclasses import replace
from unittest.mock import MagicMock, patch

from audio_directions.config import AudioError, Config, VoiceConfig
from audio_directions.controls import play_pcm, playback_hook, playback_start
from audio_directions.handsfree import Session, WakeDetector
from audio_directions.vad import FRAME_BYTES, RATE, Utterance, WebRtcVad

SILENCE = bytes(FRAME_BYTES)
SPEECH = struct.pack("<320h", *[int(5000 * math.sin(2 * math.pi * 180 * i / RATE)) for i in range(320)])
WAKE = b"\x01\x00" * 320
SETTINGS = VoiceConfig(min_speech_seconds=.04, silence_seconds=.06,
                       no_speech_seconds=.2, max_seconds=1)


class FakeMic:
    def __init__(self, frames):
        self.frames = deque(frames)
        self.flushes = 0

    def flush(self):
        # Scripted frames represent future input, not previously queued audio.
        self.flushes += 1

    def read(self, timeout=.05):
        if not self.frames:
            raise AssertionError("Unexpected read: fake microphone script exhausted")
        return self.frames.popleft()


def session(frames=(), config=SETTINGS):
    mic = FakeMic(frames)
    wake = MagicMock()
    wake.process.side_effect = lambda pcm: 0.9 if pcm == WAKE else 0.0
    vad = MagicMock()
    vad.is_speech.side_effect = lambda pcm: pcm == SPEECH
    keys = MagicMock()
    keys.poll.return_value = None
    result = Session(config, Config("fake", "voice"), mic, wake, vad, keys, MagicMock())
    result.tone = MagicMock()
    return result


class EndpointTests(unittest.TestCase):
    def test_real_vad_synthetic_audio(self):
        vad = WebRtcVad(2)
        self.assertFalse(vad.is_speech(SILENCE))
        rng = random.Random(7)
        # Voiced harmonics with changing pitch/amplitude, plus quiet noise.
        decisions = []
        for n in range(30):
            pcm = struct.pack("<320h", *[
                int(5000 * math.sin(2 * math.pi * (120 + n * 2) * (n * 320 + i) / RATE)
                    + 1400 * math.sin(2 * math.pi * 730 * i / RATE) + rng.uniform(-100, 100))
                for i in range(320)])
            decisions.append(vad.is_speech(pcm))
        self.assertTrue(any(decisions))
        for _ in range(50):
            last = vad.is_speech(SILENCE)
        self.assertFalse(last)
        with self.assertRaises(ValueError):
            vad.is_speech(b"short")

    def test_silence_ends_speech_and_wav_format(self):
        clip = Utterance(SETTINGS)
        for pcm, speech in [(SPEECH, True)] * 4 + [(SILENCE, False)] * 3:
            clip.feed(pcm, speech)
        self.assertEqual(clip.reason, "silence")
        with wave.open(io.BytesIO(clip.wav()), "rb") as wav:
            self.assertEqual((wav.getframerate(), wav.getnchannels(), wav.getsampwidth()), (RATE, 1, 2))
            self.assertEqual(wav.getnframes(), 7 * 320)

    def test_no_speech_timeout_and_short_noise_rejection(self):
        for noisy in (False, True):
            clip = Utterance(SETTINGS)
            for i in range(10):
                speech = noisy and i % 2 == 0
                clip.feed(SPEECH if speech else SILENCE, speech)
            self.assertEqual(clip.reason, "no_speech")
            self.assertEqual(clip.wav(), b"")

    def test_long_speech_survives_short_pauses_until_cap(self):
        clip = Utterance(SETTINGS)
        for i in range(49):
            clip.feed(SPEECH, i % 10 != 9)
            self.assertIsNone(clip.reason)
        self.assertEqual(clip.feed(SPEECH, True), "max_length")

    def test_settings_validation(self):
        for kwargs in ({"threshold": 2}, {"silence_seconds": 0}, {"max_seconds": float("inf")},
                       {"vad_mode": 5}, {"max_seconds": .1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(AudioError):
                VoiceConfig(**kwargs)


class SessionTests(unittest.TestCase):
    @patch("audio_directions.handsfree.transcribe_wav", return_value="hello")
    def test_wake_then_only_post_wake_clip_uploads(self, upload):
        s = session([SILENCE, WAKE] + [SPEECH] * 4 + [SILENCE] * 3)
        def check_tone():
            upload.assert_not_called()
        s.tone.side_effect = check_tone
        self.assertEqual(s.listen(), "hello")
        self.assertEqual(s.wake.process.call_count, 2)
        s.tone.assert_called_once()
        with wave.open(io.BytesIO(upload.call_args.args[0]), "rb") as wav:
            self.assertEqual(wav.readframes(320), SPEECH)

    @patch("audio_directions.handsfree.transcribe_wav", return_value="hello")
    def test_timeout_announces_without_upload_then_retries(self, upload):
        s = session([WAKE] + [SILENCE] * 10 + [WAKE] + [SPEECH] * 3 + [SILENCE] * 3)
        s.speak = MagicMock(side_effect=lambda text: upload.assert_not_called())
        self.assertEqual(s.listen(), "hello")
        self.assertIn("didn't catch", s.speak.call_args.args[0])
        upload.assert_called_once()

    @patch("audio_directions.handsfree.transcribe_wav", return_value="hello")
    def test_max_length_not_uploaded(self, upload):
        s = session([WAKE] + [SPEECH] * 50 + [WAKE] + [SPEECH] * 3 + [SILENCE] * 3)
        s.speak = MagicMock(side_effect=lambda text: upload.assert_not_called())
        self.assertEqual(s.listen(), "hello")
        self.assertIn("too long", s.speak.call_args.args[0])
        upload.assert_called_once()

    @patch("audio_directions.handsfree.transcribe_wav")
    def test_empty_and_error_transcripts_are_announced(self, upload):
        utterance = [WAKE] + [SPEECH] * 3 + [SILENCE] * 3
        s = session(utterance * 3)
        s.speak = MagicMock()
        upload.side_effect = ["", AudioError("quota"), "okay"]
        self.assertEqual(s.listen(), "okay")
        self.assertEqual(s.speak.call_count, 2)

    def test_half_duplex_never_calls_detector(self):
        s = session([WAKE])
        self.assertFalse(s.interrupt())
        s.wake.process.assert_not_called()
        self.assertEqual(s.mic.flushes, 1)

    def test_score_threshold_and_reset_between_sessions(self):
        s = session([SILENCE, WAKE])
        s.wait_for_trigger()
        s.wake.reset.assert_called_once()
        self.assertEqual(s.wake.process.call_count, 2)
        s.wake.process.side_effect = None
        s.wake.process.return_value = SETTINGS.threshold - .01
        self.assertFalse(s.detected(WAKE))
        s.wake.process.return_value = SETTINGS.threshold
        self.assertTrue(s.detected(WAKE))

    def test_record_reblocks_80ms_input_into_20ms_vad_frames(self):
        s = session([SPEECH * 4, SILENCE * 4])
        clip = s.record()
        self.assertEqual(clip.reason, "silence")
        self.assertEqual(s.vad.is_speech.call_count, 7)
        self.assertTrue(all(len(call.args[0]) == FRAME_BYTES for call in s.vad.is_speech.call_args_list))

    def test_barge_in_stops_and_sets_next_capture(self):
        s = session([WAKE], replace(SETTINGS, barge_in=True))
        self.assertTrue(s.interrupt())
        self.assertTrue(s.pending)
        s.wait_for_trigger()  # No second wake or microphone read needed.
        self.assertFalse(s.pending)

    def test_key_stop_and_push_to_talk(self):
        s = session()
        s.keys.poll.return_value = "x"
        self.assertTrue(s.interrupt())
        self.assertFalse(s.pending)
        s.keys.poll.return_value = "p"
        self.assertTrue(s.interrupt())
        self.assertTrue(s.pending)
        s.pending = False
        s.wait_for_trigger()
        s.wake.process.assert_not_called()

    @patch("audio_directions.handsfree.speak_directions", side_effect=AudioError("quota"))
    def test_tts_outage_has_audible_fallback_and_restores_hooks(self, speak):
        s = session()
        s.speak("try again")
        s.tone.assert_called_once_with(frequency=330)
        self.assertIsNone(playback_hook.get())
        self.assertIsNone(playback_start.get())



class PlaybackTests(unittest.TestCase):
    @patch("audio_directions.tts.Config.load", return_value=Config("fake", "voice"))
    @patch("audio_directions.tts.sounddevice")
    @patch("audio_directions.tts.post")
    @patch("audio_directions.tts.play_pcm", return_value=False)
    def test_interruption_skips_remaining_direction_steps(self, playback, post, sd, config):
        from audio_directions.tts import speak_directions
        post.return_value.content = b"\0\0" * 100
        speak_directions({"steps": [{"text": "first"}, {"text": "second"}]})
        post.assert_called_once()

    @patch("audio_directions.controls.Keys")
    def test_key_aborts_queued_audio(self, keys):
        keys.return_value.__enter__.return_value.poll.side_effect = [None, "x"]
        sd = MagicMock()
        self.assertFalse(play_pcm(sd, bytes(24000), 24000))
        stream = sd.RawOutputStream.return_value.__enter__.return_value
        self.assertEqual(stream.write.call_count, 1)
        stream.abort.assert_called_once()

    @patch("audio_directions.controls.Keys")
    def test_wake_interrupt_aborts_playback(self, keys):
        s = session([WAKE], replace(SETTINGS, barge_in=True))
        token = playback_hook.set(s.interrupt)
        try:
            sd = MagicMock()
            self.assertFalse(play_pcm(sd, bytes(24000), 24000))
            sd.RawOutputStream.return_value.__enter__.return_value.abort.assert_called_once()
            self.assertTrue(s.pending)
        finally:
            playback_hook.reset(token)


if __name__ == "__main__":
    unittest.main()
