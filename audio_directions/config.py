"""All provider configuration. Only this folder's .env is loaded."""
import os
import math
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent


class AudioError(RuntimeError):
    """An actionable configuration, provider, or device failure."""


@dataclass(frozen=True)
class Config:
    api_key: str = field(repr=False)
    voice_id: str = ""
    tts_model: str = "eleven_flash_v2_5"
    stt_model: str = "scribe_v2"
    timeout_seconds: float = 60.0

    @classmethod
    def load(cls):
        load_dotenv(ROOT / ".env", override=False)
        key = os.getenv("ELEVENLABS_API_KEY", "").strip()
        if not key or key == "your_api_key_here":
            raise AudioError("Set ELEVENLABS_API_KEY in audio_directions/.env or your environment.")
        return cls(
            api_key=key,
            voice_id=os.getenv("ELEVENLABS_VOICE_ID", "").strip(),
            tts_model=os.getenv("ELEVENLABS_TTS_MODEL_ID", "eleven_flash_v2_5").strip(),
            stt_model=os.getenv("ELEVENLABS_STT_MODEL_ID", "scribe_v2").strip(),
        )


@dataclass(frozen=True)
class VoiceConfig:
    model_path: str = ""
    keyword: str = "hey_jarvis"
    threshold: float = 0.6
    silence_seconds: float = 0.9
    min_speech_seconds: float = 0.25
    max_seconds: float = 30.0
    no_speech_seconds: float = 5.0
    vad_mode: int = 2
    barge_in: bool = False

    def __post_init__(self):
        if not math.isfinite(self.threshold) or not 0 < self.threshold <= 1:
            raise AudioError("WAKE_THRESHOLD must be greater than 0 and at most 1.")
        if self.vad_mode not in range(4):
            raise AudioError("VAD_MODE must be 0, 1, 2, or 3.")
        for value in (self.silence_seconds, self.min_speech_seconds,
                      self.max_seconds, self.no_speech_seconds):
            if not math.isfinite(value) or value <= 0:
                raise AudioError("Recording durations must be positive finite seconds.")
        if max(self.min_speech_seconds, self.no_speech_seconds) > self.max_seconds:
            raise AudioError("Minimum speech and no-speech timeout must not exceed max recording length.")

    @classmethod
    def load(cls):
        load_dotenv(ROOT / ".env", override=False)
        try:
            return cls(
                model_path=os.getenv("WAKE_MODEL_PATH", "").strip(),
                keyword=os.getenv("WAKE_KEYWORD", "hey_jarvis").strip(),
                threshold=float(os.getenv("WAKE_THRESHOLD", "0.6")),
                silence_seconds=float(os.getenv("VAD_SILENCE_SECONDS", "0.9")),
                min_speech_seconds=float(os.getenv("VAD_MIN_SPEECH_SECONDS", "0.25")),
                max_seconds=float(os.getenv("VAD_MAX_SECONDS", "30")),
                no_speech_seconds=float(os.getenv("VAD_NO_SPEECH_SECONDS", "5")),
                vad_mode=int(os.getenv("VAD_MODE", "2")),
                barge_in=os.getenv("VOICE_BARGE_IN", "false").lower() in ("true", "1", "yes"),
            )
        except ValueError as exc:
            raise AudioError("Invalid numeric wake/VAD setting in audio_directions/.env.") from exc
