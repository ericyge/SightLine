# Sightline

Audio-first AI companion for blind and low-vision people. A phone camera does two things:

1. **Memory:** about once a second it finds watched items (keys, wallet, phone, glasses, medication) and saves each sighting (room, spot, time, small thumbnail), automatically discovering or matching stable landmark-backed places per object (or accepting taught places/manual links). Ask "Where's my wallet?" and hear its last-seen location.
2. **Guidance:** beeps pan left/right toward the item and speed up as it gets closer, plus spoken clock-face cues ("Wallet at 2 o'clock", "Reach out now").

Frames are discarded after processing; only structured memory and object crops are stored. It runs end to end in **mock mode with no API keys**. Camera localization (without a watched object), room-to-room routing, and ElevenLabs are still future work.

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
python -m venv venv  # only if no root venv exists yet
.\venv\Scripts\python -m pip install -r backend\requirements.txt
cd backend
..\venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
```

**Terminal 2: frontend (http://localhost:5173)**

```powershell
cd web
npm install
npm run dev
```

Open <http://localhost:5173/dashboard> on your laptop and <http://localhost:5173/phone> in another tab (allow the camera). The page shows a **MOCK MODE** badge. Mock detections are fake and deterministic: with no real camera needed the backend invents positions for every watched item.

Try it: on `/phone` hold the big button and say "where's my wallet" (or, in browsers without speech recognition, type it in the box), then "find my wallet" to start guidance with panned beeps. Watch `/dashboard` update.

macOS/Linux: use the existing root environment: `source venv/bin/activate`, then
`cd backend` and `python -m uvicorn app.main:app --reload --port 8000`. On a fresh checkout
only, create it first with `python3 -m venv venv` and install `backend/requirements.txt`.
The mode in `.env` applies; use `PERCEPTRON_MODE=mock` for mock trials and separate test storage.

### Drive mock guidance by hand

Force the detected x position (0 = left edge, 1 = right edge) and size:

```powershell
curl.exe -X POST -H "X-Mock-Item-X: 0.8" -F item=wallet -F "frame=@some.jpg;type=image/jpeg" http://localhost:8000/api/guide
```

`X-Mock-Item-H: 0.4` makes the box big enough to count as "reach".

### Tests

```powershell
cd backend
..\venv\Scripts\python -m pytest
```

### Testing on a real phone (needs HTTPS for the camera)

The Vite dev server proxies `/api` and `/ws` to FastAPI, so **one** ngrok tunnel to the Vite port is enough:

```powershell
ngrok http 5173
```

Open `https://<your-subdomain>.ngrok-free.app/phone` on the phone. On iPhone, the first press of "Hold to talk" asks for motion-sensor permission. Do not tunnel port 8000.

### Real Perceptron (optional, role A)
<<<<<<< HEAD
<<<<<<< HEAD

Copy `.env.example` to `.env`, set `PERCEPTRON_API_KEY` and `PERCEPTRON_MODE=perceptron`, and `pip install "perceptron>=0.4.0"`. The real client is not implemented yet (`TODO(A)` in `backend/app/vision.py`). The frontend never calls Perceptron; the backend owns the key. Never commit `.env`.

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
=======
Copy `.env.example` to `.env`, set `PERCEPTRON_API_KEY` and `PERCEPTRON_MODE=perceptron`, and restart the backend. The client in `backend/app/vision/vision.py` is implemented and tested offline against the installed SDK. Live accuracy and latency still need an API key. The frontend never calls Perceptron; the backend owns the key. Never commit `.env`.
=======
If `.env` does not exist, copy `.env.example` to it. Set `PERCEPTRON_API_KEY` and `PERCEPTRON_MODE=perceptron`, and restart the backend. The client in `backend/app/vision/vision.py` is implemented; a first real clip succeeded, while broader accuracy and latency remain to be measured. The frontend never calls Perceptron; the backend owns the key. Never commit `.env`.
>>>>>>> 7b28120 (Memory testing)

See [the vision guide](docs/VISION.md) for recorded-video ingestion, latency benchmarks, personal item reference photos, and macOS setup. All commands also support mock mode without a key.

### Teach and inspect location memory

See [the memory guide](docs/MEMORY.md) for an isolated mock walkthrough, the example map,
and how to assign your existing real video sightings without running Perceptron again.
From `backend/`, `../venv/bin/python -m app.memory.cli where wallet` shows saved sightings
and the newest sighting's destination. `python -m app.memory.cli --help` lists teaching,
map inspection, and assignment commands (with the root environment activated).

## Layout
| Path | What |
|---|---|
| `backend/app/` | FastAPI app: `main.py` routes, `guidance.py` pure math, `vision/` client and video tools, `memory/` places and observation pipeline, `frames.py`, `db.py`, `events.py`, `models.py` |
| `web/src/phone/` | Phone UI, beep engine, speech, gyroscope, API wrappers |
| `web/src/dashboard/` | Judge-facing live dashboard |
| `purple/` | Purple agent text files (instructions, knowledge, sample profile) |
| `docs/PROJECT_OVERVIEW.md` | Problem, architecture, team, tracks, status |
| `docs/CONTRACT.md` | API contract + how the scaffold resolves open details |
| `docs/ROLES.md` | Who owns what, TODO checklists, checkpoints |
>>>>>>> bcdbc51 (Initial testing of Perceptron VLM success)

Read `docs/ROLES.md` next, then search for your `TODO(A|B|C|D)`.
