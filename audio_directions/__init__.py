"""Standalone ElevenLabs audio functions; no application or Purple networking."""
from .tts import speak_directions
from .stt import listen_and_transcribe

__all__ = ["speak_directions", "listen_and_transcribe"]
