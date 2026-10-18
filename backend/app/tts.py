"""Server-side text-to-speech for the phone (POST /api/speak).

TTS_PROVIDER=browser (default): nothing happens here; phones use their built-in voice.
TTS_PROVIDER=elevenlabs (needs ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID): the reply text is sent to
ElevenLabs and mp3 comes back. Per ElevenLabs' docs (api-reference/text-to-speech/convert):
POST {base}/text-to-speech/{voice_id}, header xi-api-key, query output_format (default mp3_44100_128),
JSON body {text, model_id}.

Privacy: only the reply text is sent (never the user's audio or camera frames). Neither the text nor
the key is ever logged. Short phrases are cached in memory (keyed by a hash), so repeated cues like
"Reach out now" are instant and free.
"""
import hashlib
import logging
import os
import threading
from collections import OrderedDict
from urllib.parse import quote

import httpx

from .net import VERIFY

log = logging.getLogger("sightline.tts")

DEFAULT_BASE = "https://api.elevenlabs.io/v1"
# eleven_flash_v2_5 is the low-latency model kaustubh-branch uses (audio_directions/config.py), but the
# ElevenLabs convert page only shows eleven_multilingual_v2 (their documented default).
# TODO: confirm eleven_flash_v2_5 on your account; override with ELEVENLABS_TTS_MODEL.
DEFAULT_MODEL = "eleven_flash_v2_5"
OUTPUT_FORMAT = "mp3_44100_128"     # ElevenLabs' documented default; also the one that needs no paid tier
MEDIA_TYPE = "audio/mpeg"           # the docs do not state a MIME type; we ask for mp3
DEFAULT_TIMEOUT_S = 5.0
MAX_CACHED_CHARS = 180              # cache both short cues and multi-word navigation instructions
CACHE_ENTRIES = 200
MAX_CACHED_BYTES = 500_000

# Fixed guidance cues (see backend/app/guidance.py and main.py). Pre-rendered at startup so they play instantly.
PREWARM_PHRASES = [
    "Reach out now",
    "Stopped.",
    "Lost it. Turn slowly left.",
    "Lost it. Turn slowly right.",
    "Stop walking. Turn slowly right in place with your phone pointed ahead.",
    "Turn left in place, then walk straight ahead.",
    "Turn right in place, then walk straight ahead.",
    "Walk straight ahead a few steps.",
    "Stop walking. Reach straight in front of you.",
    "Stop walking. Reach in front of you, slightly to your left.",
    "Stop walking. Reach in front of you, slightly to your right.",
    "Stop walking. Turn slightly left and reach in front of you.",
    "Stop walking. Turn slightly right and reach in front of you.",
    "Stop. Reach straight in front of you now.",
    "Stop. Reach in front of you to your left.",
    "Stop. Reach in front of you to your right.",
    "Step forward and reach straight in front of you.",
    "Turn slightly left and step forward.",
    "Turn slightly right and step forward.",
    "Turn left and step forward.",
    "Turn right and step forward.",
    "Walk straight ahead.",
    "Turn left.",
    "Turn right.",
]


class TTSError(Exception):
    """Any failure synthesizing (network, HTTP status, empty audio). Message is safe to log."""


class AudioCache:
    """Thread-safe LRU of short phrases -> mp3 bytes. Keys are hashes, so the text itself is not kept."""

    def __init__(self, entries: int = CACHE_ENTRIES) -> None:
        self._lock = threading.Lock()
        self._items: OrderedDict[str, bytes] = OrderedDict()
        self._entries = entries

    @staticmethod
    def key(text: str, voice: str, model: str) -> str:
        norm = " ".join(text.split()).casefold()
        return hashlib.sha256(f"{OUTPUT_FORMAT}|{voice}|{model}|{norm}".encode()).hexdigest()

    def get(self, key: str) -> bytes | None:
        with self._lock:
            audio = self._items.get(key)
            if audio is not None:
                self._items.move_to_end(key)
            return audio

    def put(self, key: str, audio: bytes) -> None:
        if len(audio) > MAX_CACHED_BYTES:
            return
        with self._lock:
            self._items[key] = audio
            self._items.move_to_end(key)
            while len(self._items) > self._entries:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        return len(self._items)


CACHE = AudioCache()


class ElevenLabsTTS:
    """ElevenLabs text-to-speech. Needs ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID."""
    provider = "elevenlabs"

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport  # tests inject httpx.MockTransport; no real network

    @staticmethod
    def api_key() -> str:
        key = os.getenv("ELEVENLABS_API_KEY", "").strip()
        return "" if key in ("", "your_api_key_here") else key

    @staticmethod
    def voice_id() -> str:
        voice = os.getenv("ELEVENLABS_VOICE_ID", "").strip()
        return "" if voice in ("", "your_voice_id_here") else voice

    @staticmethod
    def model() -> str:
        return os.getenv("ELEVENLABS_TTS_MODEL") or DEFAULT_MODEL

    def _key(self, text: str) -> str:
        return CACHE.key(text, self.voice_id(), self.model())

    def cached(self, text: str) -> bytes | None:
        """The cached mp3 for this phrase, or None. Never calls ElevenLabs."""
        return CACHE.get(self._key(text)) if len(text) <= MAX_CACHED_CHARS else None

    def speak(self, text: str) -> bytes:
        """mp3 bytes for text (from the cache when short and seen before). Raises TTSError."""
        hit = self.cached(text)
        if hit is not None:
            return hit
        key, voice = self.api_key(), self.voice_id()
        if not key or not voice:
            raise TTSError("ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID must be set")
        base = (os.getenv("ELEVENLABS_BASE_URL") or DEFAULT_BASE).rstrip("/")
        timeout = float(os.getenv("TTS_TIMEOUT_S") or DEFAULT_TIMEOUT_S)
        try:
            with httpx.Client(timeout=timeout, transport=self._transport, verify=VERIFY) as c:
                r = c.post(f"{base}/text-to-speech/{quote(voice, safe='')}",
                           params={"output_format": OUTPUT_FORMAT}, headers={"xi-api-key": key},
                           json={"text": text, "model_id": self.model()})
            r.raise_for_status()
            audio = r.content
        except httpx.TimeoutException:
            raise TTSError("timeout") from None
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            raise TTSError("quota or rate limit" if code in (402, 429) else
                           "auth denied" if code in (401, 403) else f"HTTP {code}") from None
        except httpx.HTTPError as e:
            raise TTSError(f"network ({type(e).__name__})") from None
        if not audio:
            raise TTSError("empty audio")
        if len(text) <= MAX_CACHED_CHARS:
            CACHE.put(self._key(text), audio)
        return audio


def tts_enabled() -> bool:
    return (os.getenv("TTS_PROVIDER", "browser").lower() == "elevenlabs"
            and bool(ElevenLabsTTS.api_key()) and bool(ElevenLabsTTS.voice_id()))


def get_tts() -> ElevenLabsTTS | None:
    """The client when TTS is switched on and fully configured, else None (phones use their own voice)."""
    return ElevenLabsTTS() if tts_enabled() else None


def guidance_cues_mode() -> str:
    """TTS_GUIDANCE: how guidance cues ("Wallet at 2 o'clock") are spoken.
    'always' (default): in the ElevenLabs voice, like every other reply (the phone falls back to its own voice
    only if ElevenLabs fails or is too slow), so you hear ONE voice. 'cached': ElevenLabs only for audio the
    server already has (faster, but the phone's own voice is heard for the rest). 'never': always the phone's voice."""
    value = os.getenv("TTS_GUIDANCE", "always").lower()
    return value if value in ("always", "cached", "never") else "always"


def prewarm(client: ElevenLabsTTS | None = None) -> int:
    """Render the fixed guidance cues into the cache. Failures are ignored. Returns how many are cached."""
    client = client or get_tts()
    if client is None:
        return 0
    for phrase in PREWARM_PHRASES:
        try:
            client.speak(phrase)
        except TTSError as e:
            log.warning("TTS prewarm skipped (%s)", e)
            break
    return sum(1 for p in PREWARM_PHRASES if client.cached(p) is not None)
