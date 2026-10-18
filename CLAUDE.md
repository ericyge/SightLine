# Sightline: project context for Claude Code

Read `AGENTS.md`, root `README.md`/`SUMMARY.md`, and all Markdown under `docs/` before changes.
`docs/MEMORY.md` describes taught and automatic per-object place memory; camera localization/routing and
ElevenLabs are still pending. Use the existing root `venv`, not `backend/.venv`.

36-hour hackathon project (DubHacks 2026). Audio-first AI companion for blind and low-vision people. A phone camera (head/chest mounted) does:
1. **Memory:** about once a second, find watched items (keys, wallet, phone, glasses, medication) and save each sighting with room, spot ("on the desk, next to the lamp") and time. Voice query "Where's my wallet?" -> "In your bedroom, on the desk, next to the lamp, about 4 minutes ago."
2. **Guidance:** back in the room, beeps pan left/right toward the item and speed up as it gets closer, plus clock-face cues ("Wallet at 2 o'clock", "Reach out now"); a closed loop until the user reaches the item.

Differentiator vs Ray-Ban Meta / Google Astra: persistent memory over hours + active steering, designed blind-first. Privacy: store sightings (item, place, time, small thumbnail), never video; frames are discarded after processing. Out of scope: glasses hardware, face recognition, outdoor navigation.

Tracks: Community Kitchen (main), Perceptron (our vision API), nebulaONE (a Purple agent, built in Purple's UI; only text files live in `purple/`), SideShift, DubHacks Next.

## Stack
- Backend: Python 3.11+, FastAPI, uvicorn, SQLite (stdlib), pydantic, python-dotenv, pytest. In `backend/`.
- Frontend: React + Vite (JavaScript), plain CSS, no UI framework. In `web/`.
- Browser APIs: getUserMedia, Web Audio (StereoPannerNode), SpeechSynthesis, SpeechRecognition, DeviceOrientation.
- Vision: Perceptron SDK pinned to `perceptron==0.4.0`, key `PERCEPTRON_API_KEY`, default model `perceptron-mk1.5`. Other candidate models require account availability and structured-output verification; see `docs/VISION.md`.
- Development supports macOS/Linux and Windows/PowerShell. Phone camera needs HTTPS: one `ngrok http 5173` (Vite proxies /api and /ws to :8000).

## Rules
- `docs/CONTRACT.md` is the API contract. Keep field names EXACTLY; update the doc when behavior changes.
- ALL Perceptron calls live in the `backend/app/vision/` package (`vision.py` = the full client and the mock; `live.py` = the fast detect-only client). Default is `MockVision` (`PERCEPTRON_MODE=mock`); `perceptron` = full client (places, localization; provider failures are HTTP 503); `live` = fast client (one call in flight, errors mean "not seen", dropped frames answer 204). The repo must always run with no keys. Do not guess SDK signatures: verify against the PyPI page/docs or leave a TODO.
- ALL language-model calls live in `backend/app/llm.py` (default `KeywordLLM`, no keys; any failure falls back to it). Never log tokens, request bodies or model replies. Item/room names from a model are validated against the profile, and location answers come from stored sightings, never the model.
- ALL text-to-speech provider calls live in `backend/app/tts.py` (default: the phone's built-in voice). Only reply text is sent; never log it. Guidance cues stay on the built-in voice unless cached. Only the phone speaks, never the dashboard.
- ALL speech-to-text provider calls live in `backend/app/stt.py` (default: browser recognition, no keys). Audio is never stored or logged; only the transcript goes on to the LLM.
- `backend/app/guidance.py` stays pure (no I/O, no clock reads); every function has tests in `backend/tests/test_guidance.py`.
- The frontend never calls Perceptron. Never commit `.env` or keys. Never log frames or full keys. Frames are never written to disk (only thumbnail crops).
- Accessibility on the phone page: large touch targets, ARIA labels, aria-live status, nothing visual-only.
- Keep functions small, short docstrings, no framework sprawl, no unrequested features.
- Ownership: A vision/memory, B guidance/audio, C app/dashboard, D Purple/product. See `docs/ROLES.md`; follow `TODO(A|B|C|D)` markers.

## Commands (PowerShell, from repo root)
- Backend: `cd backend; ..\venv\Scripts\python -m uvicorn app.main:app --reload --port 8000`
- Tests: `cd backend; ..\venv\Scripts\python -m pytest`
- Frontend: `cd web; npm run dev` (build check: `npm run build`)
