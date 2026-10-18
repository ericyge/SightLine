# Sightline

Audio-first AI companion for blind and low-vision people. A phone camera does two things:

1. **Memory:** about once a second it finds watched items (keys, wallet, phone, glasses, medication) and saves each sighting (room, spot, time, small thumbnail). Ask "Where's my wallet?" and hear the answer.
2. **Guidance:** beeps pan left/right toward the item and speed up as it gets closer, plus spoken clock-face cues ("Wallet at 2 o'clock", "Reach out now").

Frames are discarded after processing; only item/place/time/thumbnail are stored. It runs end to end in **mock mode with no API keys**.

For the full picture (problem, architecture, team, tracks, status) read [docs/PROJECT_OVERVIEW.md](docs/PROJECT_OVERVIEW.md).

## Mobile app (React Native + Expo)

The native phone app lives in `mobile/`. It currently shows the rear camera with
accessible permission controls. To run it on your phone with Expo Go:

```powershell
cd mobile
npm ci
npm start
```

Scan the QR code with your phone on the same Wi-Fi network and allow camera access.
See [mobile/README.md](mobile/README.md) for setup and current scope. The mobile
camera preview runs independently; backend memory, speech, and guidance are not
connected to the native app yet.

## Browser prototype quickstart (PowerShell, about 2 minutes)

Needs Python 3.11+ and Node 18+. Two terminals, from the repo root.

**Terminal 1: backend (http://localhost:8000)**

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
```

**Terminal 2: frontend (http://localhost:5173)**

```powershell
cd web
npm install
npm run dev
```

Open <http://localhost:5173/dashboard> on your laptop and <http://localhost:5173/phone> in another tab (allow the camera). The page shows a **MOCK MODE** badge. Mock detections are fake and deterministic: with no real camera needed the backend invents positions for every watched item.

Try it: on `/phone` hold the big button and say "where's my wallet" (or, in browsers without speech recognition, type it in the box), then "find my wallet" to start guidance with panned beeps. Watch `/dashboard` update.

macOS/Linux: activate with `source .venv/bin/activate` and use `python`/`pip` instead of `.\.venv\Scripts\python`.

### Drive mock guidance by hand

Force the detected x position (0 = left edge, 1 = right edge) and size:

```powershell
curl.exe -X POST -H "X-Mock-Item-X: 0.8" -F item=wallet -F "frame=@some.jpg;type=image/jpeg" http://localhost:8000/api/guide
```

`X-Mock-Item-H: 0.4` makes the box big enough to count as "reach".

### Tests

```powershell
cd backend
.\.venv\Scripts\python -m pytest
```

### Testing on a real phone (needs HTTPS for the camera)

The Vite dev server proxies `/api` and `/ws` to FastAPI, so **one** ngrok tunnel to the Vite port is enough:

```powershell
ngrok http 5173
```

Open `https://<your-subdomain>.ngrok-free.app/phone` on the phone. On iPhone, the first press of "Hold to talk" asks for motion-sensor permission. Do not tunnel port 8000.

### Real vision with Perceptron (live mode)
Default is mock (fake detections, no key). To use real Perceptron, add to `.env` (never committed) and restart the backend:
```
PERCEPTRON_MODE=live
PERCEPTRON_API_KEY=...
# PERCEPTRON_MODEL=            # blank = perceptron-mk1, the id Perceptron's docs confirm
# GUIDE_MAX_FPS=3              # guidance frames per second per phone; tune after measuring
```
`pip install -r backend/requirements.txt` installs the `perceptron` SDK. The frontend never calls Perceptron; the backend owns the key.

**Check your key, the coordinates and the latency first** (sends one image to Perceptron):
```powershell
cd backend
.\.venv\Scripts\python scripts\perceptron_probe.py C:\path\to\frame.jpg "red leather wallet"
.\.venv\Scripts\python scripts\perceptron_probe.py C:\path\to\frame.jpg "red leather wallet" --repeat 20
```
It prints the raw box (0-1000 grid), the converted 0..1 box, center x, height, and elapsed time; `--repeat` adds p50/p95.

How live mode behaves: one Perceptron call at a time. A frame that arrives while another is being processed is dropped (the API answers `204`, the phone keeps its last result and local beeps). A timeout (2 s), 429 or any error counts as "not seen" and never crashes a request. The dashboard shows live p50/p95 latency, errors and dropped frames.

### Natural conversation (optional)
`POST /api/chat` works out what the user wants from free speech. It runs with **no keys** using keyword matching (`LLM_PROVIDER=mock`, the default). To use a real model, set these in `.env` and restart the backend.

Provider order is **purple -> gemini -> keyword**. The chain starts at `LLM_PROVIDER` and falls through the later ones on any error, 3 s timeout or missing key, always ending at keyword matching. The dashboard log shows which one answered (`purple`, `gemini`, `keyword_fallback`, `mock`).
```
# Gemini (Google AI Studio key; uses Google's OpenAI-compatible endpoint)
LLM_PROVIDER=gemini
GEMINI_API_KEY=...
GEMINI_MODEL=...            # see Google's "OpenAI compatibility" docs; model names change
# GEMINI_BASE_URL is optional (blank = Google's documented endpoint)

# Purple (needs API access); with LLM_PROVIDER=purple the chain is purple -> gemini -> keyword
LLM_PROVIDER=purple
PURPLE_API_TOKEN=...
PURPLE_MODEL=...            # list ids: cd backend; .\.venv\Scripts\python -m app.llm models

# or any OpenAI-compatible server
LLM_PROVIDER=openai_compat
LLM_BASE_URL=...  LLM_API_KEY=...  LLM_MODEL=...
```
Privacy: only your words, the last 6 turns, and item/alias/room names go to a provider, never camera frames, thumbnails or sighting history. Details: `docs/CONTRACT.md`.

### Speech-to-text (optional)
By default the phone page uses the browser's built-in speech recognition (Chrome/Edge; no keys). For a more reliable, any-browser option, set in `.env` and restart the backend:
```
STT_PROVIDER=elevenlabs
ELEVENLABS_API_KEY=...
```
The phone then records a short clip while you hold the button and the backend sends it to ElevenLabs for the transcript, which goes to `/api/chat` and Gemini as before. **The audio leaves your machine to ElevenLabs** (it is not stored or logged). If ElevenLabs fails, the phone falls back to browser recognition. Details: `docs/CONTRACT.md`.

### Native phone app (`mobile/`)
The Expo app in `mobile/` uses the same backend: camera frames, hold-to-talk, Gemini chat and directional beeps. It needs the backend reachable from the phone (`--host 0.0.0.0` on the same Wi-Fi, or `ngrok http 8000`); see `mobile/README.md`. Untested on real devices.

### Text-to-speech (optional ElevenLabs voice)
By default the phone speaks with its built-in voice (no keys). To hear replies in your chosen ElevenLabs voice, set in `.env` and restart the backend:
```
TTS_PROVIDER=elevenlabs
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...        # from your ElevenLabs account
# ELEVENLABS_TTS_MODEL=        # blank = eleven_flash_v2_5; see .env.example if it is rejected
# TTS_GUIDANCE=cached          # or never
```
Chat replies (the `say` from `/api/chat`) use that voice. Fast guidance cues ("Wallet at 2 o'clock") stay on the built-in voice so guidance never waits on the network. The fixed cues ("Reach out now", "Stopped.", "Lost it. Turn slowly left.") are rendered once at startup and cached in memory, so they use your voice instantly when `TTS_GUIDANCE=cached`. If ElevenLabs fails or is slow, the phone speaks the same words with its built-in voice, so it is never silent. Only the phone speaks; the dashboard is silent.

### Privacy: what leaves your machine
Frames are processed and discarded; only item, place, time and a small thumbnail are stored, never video. Per service:
| Service | When | What is sent |
|---|---|---|
| Perceptron | `PERCEPTRON_MODE=live` | the current camera frame (JPEG) and the item descriptions |
| Gemini / Purple | `LLM_PROVIDER` is not `mock` | your words, the last 6 turns, item/alias/room names. Never frames, thumbnails or sighting history |
| ElevenLabs speech-to-text | `STT_PROVIDER=elevenlabs` | your recorded voice clip |
| ElevenLabs text-to-speech | `TTS_PROVIDER=elevenlabs` | **only the reply text** to be spoken. Never your audio or camera frames |

Keys, the text being spoken and audio are never logged. Short spoken phrases are cached in server memory (as hashes), not on disk.

## Layout

| Path                       | What                                                                                                                              |
| -------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `backend/app/`             | FastAPI app: `main.py` routes, `guidance.py` pure math, `vision.py` (the only Perceptron code), `db.py`, `events.py`, `models.py` |
| `mobile/`                 | React Native + Expo mobile app; currently a camera preview with permission handling |
| `web/src/phone/`           | Phone UI, beep engine, speech, gyroscope, API wrappers                                                                            |
| `web/src/dashboard/`       | Judge-facing live dashboard                                                                                                       |
| `purple/`                  | Purple agent text files (instructions, knowledge, sample profile)                                                                 |
| `docs/PROJECT_OVERVIEW.md` | Problem, architecture, team, tracks, status                                                                                       |
| `docs/CONTRACT.md`         | API contract + how the scaffold resolves open details                                                                             |
| `docs/ROLES.md`            | Who owns what, TODO checklists, checkpoints                                                                                       |

Read `docs/ROLES.md` next, then search for your `TODO(A|B|C|D)`.
