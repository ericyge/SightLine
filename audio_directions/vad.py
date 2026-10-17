"""Pure endpoint state machine plus a small WebRTC adapter. No devices or I/O."""
import io
import math
import wave
from collections import deque

from .config import AudioError, VoiceConfig

RATE = 16000
FRAME_MS = 20
FRAME_BYTES = RATE * FRAME_MS // 1000 * 2


class WebRtcVad:
    def __init__(self, mode=2):
        try:
            import webrtcvad
        except ImportError as exc:
            raise AudioError("Install audio_directions/requirements.txt (webrtcvad-wheels missing).") from exc
        self.engine = webrtcvad.Vad(mode)

    def is_speech(self, pcm):
        if len(pcm) != FRAME_BYTES:
            raise ValueError("VAD requires 20 ms of 16 kHz mono int16 PCM.")
        return self.engine.is_speech(pcm, RATE)


class Utterance:
    """Feed 20 ms PCM frames and VAD decisions; reason is set when complete.

    Require a sustained speech run before accepting speech. Keep 200 ms pre-roll
    after activation, never pre-wake audio. Short noise bursts reset the candidate.
    """
    def __init__(self, config: VoiceConfig):
        self.config = config
        self.elapsed = self.run = self.silence = 0.0
        self.started = False
        self.reason = None
        self.frames = []
        self.pre = deque(maxlen=math.ceil(config.min_speech_seconds / .02) + 10)

    def feed(self, pcm: bytes, speech: bool):
        if len(pcm) != FRAME_BYTES:
            raise ValueError("Expected a 20 ms PCM frame.")
        if self.reason:
            return self.reason
        self.elapsed = round(self.elapsed + .02, 6)
        if not self.started:
            self.pre.append(pcm)
            self.run = round(self.run + .02, 6) if speech else 0
            if self.run >= self.config.min_speech_seconds:
                self.started = True
                self.frames.extend(self.pre)
        else:
            self.frames.append(pcm)
            self.silence = 0 if speech else round(self.silence + .02, 6)
        if not self.started and self.elapsed >= self.config.no_speech_seconds:
            self.reason = "no_speech"
        elif self.elapsed >= self.config.max_seconds:
            self.reason = "max_length" if self.started else "no_speech"
        elif self.started and self.silence >= self.config.silence_seconds:
            self.reason = "silence"
        return self.reason

    def wav(self):
        if not self.started:
            return b""
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(RATE)
            wav.writeframes(b"".join(self.frames))
        return buffer.getvalue()
