"""Sequential, complete-step speech synthesis and optional WAV output."""
import sys
import wave
from pathlib import Path
from urllib.parse import quote

from .adapter import extract_steps
from .config import AudioError, Config, ROOT
from .devices import sounddevice
from .provider import post

SAMPLE_RATE = 24000


def speak_directions(payload: dict | str | Path, *, play: bool = True,
                     save_dir: str | Path | None = None) -> list[Path]:
    """Speak steps in order; optionally save step-001.wav etc. Return saved paths.

    Blocking: each step is synthesized and played before requesting the next.
    save_dir must resolve inside audio_directions; relative paths are folder-relative.
    Existing step files are replaced. A later failure leaves earlier saved steps intact.
    """
    texts = extract_steps(payload)
    config = Config.load()
    if not config.voice_id or config.voice_id == "your_voice_id_here":
        raise AudioError("Set ELEVENLABS_VOICE_ID in audio_directions/.env.")
    if not play and save_dir is None:
        raise ValueError("Choose playback or a save_dir.")
    output = None
    if save_dir is not None:
        output = (ROOT / save_dir).resolve()
        if not output.is_relative_to(ROOT):
            raise ValueError("save_dir must be inside audio_directions/.")
        # Reject escaping symlinks before making any requests or writes.
        for i in range(1, len(texts) + 1):
            if not (output / f"step-{i:03}.wav").resolve().is_relative_to(ROOT):
                raise ValueError("Audio output symlink points outside audio_directions/.")
        output.mkdir(parents=True, exist_ok=True)
    sd = sounddevice() if play else None
    if sd:
        try:
            sd.check_output_settings(samplerate=SAMPLE_RATE, channels=1, dtype="int16")
        except Exception as exc:
            raise AudioError("Speaker unavailable or unsupported 24 kHz mono output. Check the default device.") from exc
    saved = []
    for i, text in enumerate(texts, 1):
        response = post(
            f"/text-to-speech/{quote(config.voice_id, safe='')}", config,
            params={"output_format": "pcm_24000"},
            json={"text": text, "model_id": config.tts_model},
        )
        pcm = response.content
        if not pcm or len(pcm) % 2:
            raise AudioError("ElevenLabs returned empty or invalid 16-bit PCM audio.")
        if output:
            path = output / f"step-{i:03}.wav"
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(SAMPLE_RATE)
                wav.writeframes(pcm)
            saved.append(path)
        if sd:
            if sys.byteorder != "little":
                from array import array
                samples = array("h", pcm)
                samples.byteswap()
                pcm = samples.tobytes()
            try:
                with sd.RawOutputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16") as stream:
                    stream.write(pcm)
            except Exception as exc:
                raise AudioError("Audio playback failed. Check your default speaker/output device.") from exc
    return saved
