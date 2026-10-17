# Audio directions

Standalone Python 3.11+ TTS and STT using ElevenLabs. No backend changes,
Purple client, or agent networking. The sample JSON is static input, not a mock agent.

## Setup (from repository root)

macOS/Linux:

```sh
python3 -m venv audio_directions/.venv
audio_directions/.venv/bin/python -m pip install --no-cache-dir -r audio_directions/requirements.txt
cp audio_directions/.env.example audio_directions/.env
```

Windows PowerShell:

```powershell
py -3 -m venv audio_directions/.venv
audio_directions/.venv/Scripts/python.exe -m pip install --no-cache-dir -r audio_directions/requirements.txt
Copy-Item audio_directions/.env.example audio_directions/.env
```

Edit `audio_directions/.env`: replace the key and voice placeholders.
Create an API key in ElevenLabs **Developers → API Keys**, with TTS and STT access.
Select an accessible voice under **Voices / My Voices** and copy its voice ID.
See [authentication](https://elevenlabs.io/docs/api-reference/authentication) and
[voice selection](https://elevenlabs.io/docs/eleven-api/guides/cookbooks/text-to-speech).
Environment variables take precedence over this folder's `.env`; the root `.env` is never loaded.

`config.py` centralizes defaults: `eleven_flash_v2_5` (low-latency TTS) and
`scribe_v2` (file transcription). All four settings are shown in `.env.example`.
Model IDs checked against [ElevenLabs models](https://elevenlabs.io/docs/overview/models)
and the [STT API](https://elevenlabs.io/docs/api-reference/speech-to-text/convert).
Voice/model access and credits depend on your account.

Audio uses `sounddevice` (PortAudio) with your OS default speaker/microphone.
macOS/Windows pip wheels normally supply PortAudio. Linux may require the OS package
`libportaudio2`; a source installation on macOS may require Homebrew `portaudio`.
Allow microphone access to your terminal/Python when prompted. No ffmpeg or NumPy is needed.

## Run

From the repository root, macOS/Linux:

```sh
# Speak the bundled steps, sequentially:
audio_directions/.venv/bin/python -m audio_directions tts
# Record immediately; press Enter to stop, upload to ElevenLabs, and print text:
audio_directions/.venv/bin/python -m audio_directions stt
# Save one WAV per step without a speaker:
audio_directions/.venv/bin/python -m audio_directions tts --no-play --save-dir generated
# Read a different JSON file:
audio_directions/.venv/bin/python -m audio_directions tts audio_directions/dummy_directions.json
# Offline tests (no key, microphone, credits, or sounddevice needed):
audio_directions/.venv/bin/python -B -m unittest discover -s audio_directions/tests -v
```

On Windows, replace `audio_directions/.venv/bin/python` with
`audio_directions/.venv/Scripts/python.exe` in each command.
`--save-dir generated` can also be used with playback. Paths are relative to this
folder; paths escaping it are rejected. Step filenames are overwritten on reruns.
Ctrl+C cancels. STT status goes to stderr; stdout contains only the transcript.

## Integration contract

```python
from audio_directions import speak_directions, listen_and_transcribe

speak_directions({"steps": [{"text": "Wallet slightly right."}]})
text = listen_and_transcribe()  # Caller decides what to do with this string.
```

Only `adapter.extract_steps()` interprets the upstream payload. Assumed shape:
`{"steps": [{"text": "..."}, ...]}`. Extra fields are ignored; missing/empty steps
and blank/non-string text fail before any API call. A string argument is a file
path, not serialized JSON. Text is plain speech text; no SSML processing.

Both functions block. TTS returns saved `Path` objects (or `[]` for playback only).
It requests complete 24 kHz mono, signed 16-bit little-endian PCM for each step,
then plays it fully before requesting the next. WAV saving uses Python's `wave`.
There is no token/audio streaming or automatic retry. The model's advertised
latency is not the total time to playback. See the
[TTS API](https://elevenlabs.io/docs/api-reference/text-to-speech/convert).

STT uses 16 kHz mono microphone audio, wraps it as WAV in memory, and uploads it
only to ElevenLabs after Enter. No recording or transcript is saved. Two-minute
memory cap: longer captures stop accepting audio and report an error when Enter
is pressed, rather than silently uploading a truncated recording. Silence may
return an empty string. This first interface requires an interactive terminal;
a future app can replace `_record_wav()` with its own recording controls.

Failures raise `AudioError` (provider/device/config) or `ValueError` (payload/options).
The CLI prints an error and exits nonzero; auth, quota, connectivity, and device
errors have actionable messages. Earlier played/saved steps remain if a later
step fails. Credentials and provider response bodies are not logged.
