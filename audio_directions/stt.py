"""Record until Enter, then return an ElevenLabs transcript; no forwarding."""
import io
import sys
import wave

from .config import AudioError, Config
from .devices import sounddevice
from .provider import post

SAMPLE_RATE = 16000
MAX_SECONDS = 120


def _record_wav() -> bytes:
    """Bound memory to two minutes; Enter closes the device before upload."""
    sd = sounddevice()
    chunks = []
    issues = []
    size = 0
    limit = SAMPLE_RATE * 2 * MAX_SECONDS

    def callback(data, frames, time_info, status):
        nonlocal size
        if status:
            if not issues:
                issues.append("Microphone overflow/device issue. Try recording again.")
        if size + len(data) > limit:
            if not issues:
                issues.append("Recording exceeded 120 seconds. Retry with a shorter recording.")
            raise sd.CallbackStop()
        chunks.append(bytes(data))
        size += len(data)

    try:
        with sd.RawInputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", callback=callback):
            print("Recording (up to 120 seconds). Press Enter to stop and transcribe; Ctrl+C cancels.", file=sys.stderr)
            input()
    except EOFError as exc:
        raise AudioError("STT needs an interactive terminal. Press Enter to finish recording.") from exc
    except (sd.PortAudioError, OSError) as exc:
        raise AudioError("Cannot record. Allow microphone access and check the default input device (16 kHz mono).") from exc
    if issues:
        raise AudioError(issues[0])
    pcm = b"".join(chunks)
    if not pcm:
        raise AudioError("No microphone audio captured. Record for longer before pressing Enter.")
    if sys.byteorder != "little":
        from array import array
        samples = array("h", pcm)
        samples.byteswap()
        pcm = samples.tobytes()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)
    return buffer.getvalue()


def listen_and_transcribe() -> str:
    """Blocking terminal capture; return text only (possibly empty for silence).

    Microphone WAV lives in memory. Only ElevenLabs receives it. Ctrl+C cancels
    recording without upload. No transcript is persisted or sent to Purple.
    """
    config = Config.load()  # Fail before recording if the key is absent.
    audio = _record_wav()
    response = post(
        "/speech-to-text", config,
        data={"model_id": config.stt_model, "tag_audio_events": "false", "diarize": "false"},
        files={"file": ("recording.wav", audio, "audio/wav")},
    )
    try:
        text = response.json()["text"]
    except (ValueError, KeyError, TypeError) as exc:
        raise AudioError("ElevenLabs returned an invalid transcript response (missing text).") from exc
    if not isinstance(text, str):
        raise AudioError("ElevenLabs transcript text was not a string.")
    return text.strip()
