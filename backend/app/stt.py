"""Server-side speech-to-text for the phone page (POST /api/transcribe).

STT_PROVIDER=browser (default): the server does nothing; the phone uses the browser's own speech
recognition. STT_PROVIDER=elevenlabs: the phone uploads a short WAV and this relays it to
ElevenLabs (POST {base}/speech-to-text, header xi-api-key, form fields model_id + file; per
ElevenLabs' docs and the same call kaustubh-branch's audio_directions/stt.py makes).

Privacy: audio is held in memory only, never written to disk, and sent only to ElevenLabs. Never
log audio, transcripts or the API key. No automatic retries (avoids double charges).
"""
import logging
import os
from abc import ABC, abstractmethod

import httpx

from .net import VERIFY

log = logging.getLogger("sightline.stt")

DEFAULT_BASE = "https://api.elevenlabs.io/v1"
DEFAULT_MODEL = "scribe_v2"   # the model id used in ElevenLabs' docs; override with ELEVENLABS_STT_MODEL
DEFAULT_TIMEOUT_S = 10.0
EXTENSIONS = {"audio/wav": "wav", "audio/x-wav": "wav", "audio/webm": "webm", "audio/ogg": "ogg",
              "audio/mp4": "m4a", "audio/mpeg": "mp3"}


class STTError(Exception):
    """Any failure transcribing (network, HTTP status, bad response). Message is safe to log."""


class STTClient(ABC):
    provider = "unknown"

    @abstractmethod
    def transcribe(self, audio: bytes, content_type: str = "audio/wav") -> str:
        """Return the transcript (possibly empty for silence). Raise STTError on failure."""


class ElevenLabsSTT(STTClient):
    """ElevenLabs speech-to-text. Needs ELEVENLABS_API_KEY."""
    provider = "elevenlabs"

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport  # tests inject httpx.MockTransport; no real network

    @staticmethod
    def api_key() -> str:
        key = os.getenv("ELEVENLABS_API_KEY", "").strip()
        return "" if key in ("", "your_api_key_here") else key

    def transcribe(self, audio: bytes, content_type: str = "audio/wav") -> str:
        key = self.api_key()
        if not key:
            raise STTError("ELEVENLABS_API_KEY not set")
        base = (os.getenv("ELEVENLABS_BASE_URL") or DEFAULT_BASE).rstrip("/")
        model = os.getenv("ELEVENLABS_STT_MODEL") or DEFAULT_MODEL
        timeout = float(os.getenv("STT_TIMEOUT_S") or DEFAULT_TIMEOUT_S)
        ext = EXTENSIONS.get(content_type.split(";")[0].strip().lower(), "wav")
        try:
            with httpx.Client(timeout=timeout, transport=self._transport, verify=VERIFY) as c:
                r = c.post(f"{base}/speech-to-text", headers={"xi-api-key": key},
                           data={"model_id": model, "tag_audio_events": "false", "diarize": "false"},
                           files={"file": (f"recording.{ext}", audio, content_type)})
            r.raise_for_status()
            text = r.json()["text"]
        except httpx.TimeoutException:
            raise STTError("timeout") from None
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            raise STTError("quota or rate limit" if code in (402, 429) else
                           "auth denied" if code in (401, 403) else f"HTTP {code}") from None
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            raise STTError(f"bad response ({type(e).__name__})") from None
        if not isinstance(text, str):
            raise STTError("bad response (text not a string)")
        return text.strip()


def get_stt() -> STTClient | None:
    """The configured client, or None when the phone should use browser recognition."""
    if os.getenv("STT_PROVIDER", "browser").lower() == "elevenlabs" and ElevenLabsSTT.api_key():
        return ElevenLabsSTT()
    return None
