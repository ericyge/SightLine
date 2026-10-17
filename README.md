# Sightline

Audio-first AI companion for blind and low-vision people. A phone camera does two things:

1. **Memory:** about once a second it finds watched items (keys, wallet, phone, glasses, medication) and saves each sighting (room, spot, time, small thumbnail). Ask "Where's my wallet?" and hear the answer.
2. **Guidance:** beeps pan left/right toward the item and speed up as it gets closer, plus spoken clock-face cues ("Wallet at 2 o'clock", "Reach out now").

Frames are discarded after processing; only item/place/time/thumbnail are stored. It runs end to end in **mock mode with no API keys**.

## Quickstart (PowerShell, about 2 minutes)

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

## Layout
| Path | What |
|---|---|
| `backend/app/` | FastAPI app: `main.py` routes, `guidance.py` pure math, `vision.py` (the only Perceptron code), `db.py`, `events.py`, `models.py` |
| `web/src/phone/` | Phone UI, beep engine, speech, gyroscope, API wrappers |
| `web/src/dashboard/` | Judge-facing live dashboard |
| `purple/` | Purple agent text files (instructions, knowledge, sample profile) |
| `docs/CONTRACT.md` | API contract + how the scaffold resolves open details |
| `docs/ROLES.md` | Who owns what, TODO checklists, checkpoints |

Read `docs/ROLES.md` next, then search for your `TODO(A|B|C|D)`.
