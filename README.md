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

### Real Perceptron (optional, role A)

Copy `.env.example` to `.env`, set `PERCEPTRON_API_KEY` and `PERCEPTRON_MODE=perceptron`, and `pip install "perceptron>=0.4.0"`. The real client is not implemented yet (`TODO(A)` in `backend/app/vision.py`). The frontend never calls Perceptron; the backend owns the key. Never commit `.env`.

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
