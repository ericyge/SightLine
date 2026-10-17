"""All provider configuration. Only this folder's .env is loaded."""
import os
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
