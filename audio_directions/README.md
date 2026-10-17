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
Allow microphone access to your terminal/Python when prompted. No ffmpeg is needed. The hands-free detector additionally uses NumPy and ONNX Runtime.

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
return an empty string. This manual interface requires an interactive terminal;
a future app can replace `_record_wav()` with its own recording controls.

Failures raise `AudioError` (provider/device/config) or `ValueError` (payload/options).
The CLI prints an error and exits nonzero; auth, quota, connectivity, and device
errors have actionable messages. Earlier played/saved steps remain if a later
step fails. Credentials and provider response bodies are not logged.

## Keyless hands-free demo (openWakeWord)

Wake detection runs locally with **openWakeWord 0.6.0 and ONNX Runtime CPU**.
The default pretrained stand-in is **“Hey Jarvis”**. “Hey Sightline” requires a
custom model. There is no wake-provider key, account, or runtime network call.
The three APIs and the existing TTS/STT commands retain their signatures.

### Install and download

From the repository root, in the existing environment only:

```sh
audio_directions/.venv/bin/python -m pip install --no-cache-dir -r audio_directions/requirements.txt
audio_directions/.venv/bin/python -m audio_directions.setup_wake
audio_directions/.venv/bin/python -m audio_directions.setup_wake --verify-only
audio_directions/.venv/bin/python -m audio_directions demo --debug
# Headphones required for this option:
audio_directions/.venv/bin/python -m audio_directions demo --debug --barge-in
# Offline regression suite (no .env read, mic, network, credentials or credits):
audio_directions/.venv/bin/python -B -m unittest discover -s audio_directions/tests -v
```

`setup_wake` downloads just `melspectrogram.onnx`, `embedding_model.onnx`, and
`hey_jarvis_v0.1.onnx` from the upstream v0.5.1 model release into git-ignored
`audio_directions/models/pretrained/`. It then loads the model and scores 40
synthetic frames. `--verify-only` performs no downloads. Neither command loads
`.env` or accesses a microphone. Interrupted downloads are not kept as completed
models. Runtime detection never auto-downloads missing files.

**Verification on this task:** dependency installation could not access a usable
package source, and GitHub model download failed with a DNS resolution error in
the sandbox. No alternate network route was used. Consequently actual package
import/model load/synthetic inference have NOT been verified here. Run the first
three commands above on your machine. Offline tests use fake wake engines.

**Platform choice:** ONNX avoids macOS's missing classic `tflite-runtime` wheels
and does not need TensorFlow/PyTorch. Use native arm64 Python 3.11/3.12 on Apple
Silicon, or matching x86_64 Python on Intel. For Linux x86_64/aarch64, use Python
3.11 for this pinned PyPI release: its metadata also requires `tflite-runtime`
on Linux even when selecting ONNX, and that dependency lacks Python 3.12 wheels.
Do not silently downgrade openWakeWord or replace the existing environment. If
an existing Linux venv is Python 3.12+, this requirements install needs a separate,
deliberate dependency-policy change; it is not claimed supported by this setup.
[PyPI](https://pypi.org/project/openwakeword/) ·
[Release dependency metadata](https://github.com/dscripka/openWakeWord/blob/v0.6.0/setup.py)

**Apple Silicon limitation:** upstream issue #336 reports ONNX scores near zero
on genuine wake speech. Loading/scoring silence successfully does not disprove
that problem. Validate positive recordings on the target Mac before relying on
this backend. Do not lower thresholds toward zero to hide it. This implementation
explicitly uses ONNX, without untested runtime shims or silently installing a
second inference stack. Newer upstream source uses LiteRT, but differs from the
published 0.6.0 package. [Issue #336](https://github.com/dscripka/openWakeWord/issues/336)

### Configuration and behavior

Manually merge the settings from `.env.example` into the existing `.env`; do not
copy over your ElevenLabs credentials. Set `WAKE_KEYWORD=hey_jarvis`, clear
`WAKE_MODEL_PATH` for the stand-in, and set **`WAKE_THRESHOLD=0.6`**. The previous
`WAKE_SENSITIVITY` setting is no longer read: threshold has the opposite direction
of adjustment. Unknown keyword names produce an actionable error. Environment
variables override the folder-local `.env`.

| Setting | Default | Meaning |
|---|---:|---|
| `WAKE_MODEL_PATH` | empty | Custom ONNX under audio_directions/models |
| `WAKE_KEYWORD` | hey_jarvis | Supported pretrained stand-in |
| `WAKE_THRESHOLD` | 0.6 | Score threshold, greater than 0 and at most 1 |
| `VAD_SILENCE_SECONDS` | 0.9 | Trailing silence before finishing |
| `VAD_MIN_SPEECH_SECONDS` | 0.25 | Sustained speech required |
| `VAD_MAX_SECONDS` | 30 | Maximum recording length |
| `VAD_NO_SPEECH_SECONDS` | 5 | No-speech timeout |
| `VAD_MODE` | 2 | WebRTC aggressiveness 0–3 |
| `VOICE_BARGE_IN` | false | Wake-word interruption during TTS |

Demo: wait → wake → tone → record → trailing silence → ElevenLabs STT → print →
TTS echo → repeat. Speak after the tone. **P** skips the wake word; during speech,
**any key** stops playback, and **P** also starts a new capture. Ctrl+C cleans up
mic, model, and terminal state. Keys require an interactive terminal.

Half duplex ignores wake input during TTS. Barge-in requires headphones because
there is no echo cancellation. Inference consumes 80 ms mono 16 kHz int16 blocks;
recording splits these into the unchanged 20 ms VAD frames. Debug reports actual
0–1 wake scores, threshold, VAD changes and timings, not a calibrated probability.
Detector buffers reset between listening sessions. Playback polling remains at
~20 ms writes; network synthesis is still blocking, so a key during synthesis is
handled when playback starts, not by canceling the HTTP request.

Pre-activation microphone audio is never uploaded. Wake detection only reads
local model files. P is the retained explicit activation alternative. After
activation, only the post-tone utterance is uploaded to ElevenLabs. No Purple
client or forwarding exists. The setup command's network requests download public
models and send no audio. No recording/transcript files are written by the demo.

Timeout, empty transcript, and STT errors produce spoken retry prompts. If TTS
also fails, a low tone and stderr provide fallback feedback. Maximum-length clips
are discarded with a shorter-request prompt. Devices/config failures exit clearly.
The standalone manual `stt` command still stops on Enter.

```python
from audio_directions import listen_for_wake_word_and_transcribe
text = listen_for_wake_word_and_transcribe()  # Returns text; does not echo or forward.
```

For false triggers, **raise `WAKE_THRESHOLD` first**, e.g. 0.6 → 0.7, then retest
real positives. For misses, first check the model, device, and platform issue,
then lower the threshold cautiously. For early cutoff, increase trailing silence
to 1.2–1.5 seconds; lower VAD aggressiveness for quiet speech. Short commands may
need a smaller minimum speech value. Background conversation can extend capture.

### Train “Hey Sightline”

Research checked October 17, 2026. The official
[automatic notebook](https://github.com/dscripka/openWakeWord/blob/main/notebooks/automatic_model_training.ipynb)
is still the upstream workflow, but current community reports describe broken
Colab dependency combinations. I inspected documentation, not a completed training
run; no notebook is asserted personally verified here.

The [2026 toolkit](https://github.com/saintpete/openwakeword-colab-toolkit) reports
an end-to-end run on August 1, 2026 and includes compatibility fixes plus evaluation.
Use its `hey_clem_colab.ipynb`:

1. Open [the notebook in Colab](https://colab.research.google.com/github/saintpete/openwakeword-colab-toolkit/blob/main/hey_clem_colab.ipynb), save a personal copy, and select a T4 GPU runtime.
2. Find the `%%writefile hey_clem.yaml` cell. Keep that config filename (other cells
   reference it); change these YAML values:

   ```yaml
   model_name: hey_sightline
   target_phrase: ["hey sightline"]
   custom_negative_phrases: ["hey slightly", "hey Siri", "hello Sightline"]
   ```

3. Run cells in order, including preflight, synthetic generation, augmentation,
   training, and export. Budget roughly two GPU-hours and possible paid Colab
   capacity; free sessions can expire. No training dependencies are installed here.
4. Download the final ZIP. Keep **both** `hey_sightline.onnx` and any
   `hey_sightline.onnx.data` sidecar together.

An alternative is [this 2026 trainer](https://github.com/alfiedennen/openwakeword-colab-2026),
whose author reports 75–90 minutes on Colab Pro and slower free T4 runs. Locate
`TARGET_PHRASE` and `MODEL_NAME`, set `['hey sightline']` and `'hey_sightline'`,
then run through its ONNX export. It replaces the upstream training loop;
its success report is not an independent guarantee. Runtime availability, storage,
download speeds, sample count, and retries can make either workflow take hours.

Place the exported model and sidecar (if any) under `audio_directions/models/`.
Set exactly:

```dotenv
WAKE_MODEL_PATH=models/hey_sightline.onnx
WAKE_THRESHOLD=0.6
```

Keep the downloaded mel/embedding support models as well. Export an openWakeWord
single-output classifier with a sigmoid score in [0,1], not a generic speech
model. Restart the demo. `--verify-only` checks the pretrained stand-in, not this
custom model. Use the real demo and the checklist below to evaluate the custom one.

Synthetic training accuracy is not deployment accuracy. Measure false activations
per hour on hours of representative TV, conversation, music, and noise, and recall
on held-out real utterances from multiple speakers at 1–3 meters. Do not use these
same recordings for both threshold selection and final evaluation. A short run
with zero false activations cannot establish a zero rate. Keep false triggers the
priority; retrain with hard negatives if threshold changes destroy useful recall.
“Hey sight line is” contains the phonetic wake phrase; rejecting every such
continuation is not something keyword spotting guarantees.

### Model licensing

The openWakeWord code is Apache-2.0; bundled pretrained models, including the
stand-in, are **CC BY-NC-SA 4.0**, not unrestricted commercial assets. Non-commercial
sharing can be allowed with attribution and share-alike obligations; ignoring
binaries is our conservative project policy, not a license-mandated Git rule.
All `models/` binaries remain ignored. [Upstream license statement](https://github.com/dscripka/openWakeWord/tree/v0.6.0#license)

Training your own phrase does not automatically clear the licenses of synthetic
voices, background audio, embeddings, or other training inputs. Treat the described
model workflow as non-commercial until those rights are reviewed; the notebook's
code license alone does not clear its output for a commercial app. No claim is
made that every independently trained custom model must have the same license.

## Manual microphone checklist

On macOS, select the default input/output in **System Settings → Sound**. Grant
microphone access to the app hosting Python (Terminal, iTerm, or your editor) under
**Privacy & Security → Microphone**, then restart that app if necessary. If no
permission entry appears, run the demo in Terminal to prompt for access.

| Test | Expected result |
|---|---|
| a. Say the configured wake word | Exactly one confirmation beep; then recording |
| b. Say a short sentence, then pause | Ends after ~0.9 s VAD silence, prints and echoes text |
| c. Speak a 10–20 s sentence with pauses shorter than silence setting | No mid-speech cutoff; ends after trailing silence |
| d. Wake, then say nothing | After ~5 s, “I didn't catch that”; no STT upload |
| e. Background noise/TV without the wake phrase | No activation; record and tune any false wake (TV saying the phrase can activate it) |
| f. In default mode, ask it to echo a sentence containing the wake word | It does not activate on its own TTS |
| g. With headphones and barge-in, say the wake word during a long echo | Playback stops, beep, new capture starts |
| h. Press X during speech; separately press P while waiting/during speech | X stops speech; P starts capture without a wake phrase |
| i. Say “hey slightly”, “hey Siri”, “hello Sightline” | Target: no activation; repeat/tune with the custom model |
| i. Say “hey sight line is” | May activate: it contains the phonetic wake phrase. Strict rejection of this continuation is not provided by keyword spotting |
| j. Repeat at slow/normal/fast rates, 1–3 m away, with mild noise | Target: reliable single activation; log misses and repeat negative tests after tuning |

Also verify Ctrl+C restores the terminal, a deliberate >30 s utterance gives the
length prompt, and expired provider credentials yield visible/spoken feedback as
available. Similar-phrase and distance results require the actual custom model,
microphone, room, and representative speakers; no accuracy guarantees are made.
