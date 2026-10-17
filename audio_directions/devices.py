"""Lazy device dependency so save-only TTS works without an audio device."""
from .config import AudioError


def sounddevice():
    try:
        import sounddevice as sd
        return sd
    except (ImportError, OSError) as exc:
        raise AudioError("Audio requires sounddevice and PortAudio. See audio_directions/README.md.") from exc
