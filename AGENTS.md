# Sightline: project context for Cursor

## Start every new chat with repository context
- Before planning or editing in a new chat for this repository, read this file, the root `README.md`, and `SUMMARY.md` if it exists.
- Then read `docs/README.md` and every other Markdown document under `docs/`, including subdirectories. Start with `PROJECT_OVERVIEW.md`, `CONTRACT.md`, `ROLES.md`, `VISION.md`, and `MEMORY.md`; discover any additional files rather than relying only on this list.
- Read the actual file contents using file tools; filenames and links alone do not load their contents into context. If a required document is missing or cannot be read, state that limitation rather than claiming it was loaded.
- Use these documents to understand current behavior, interfaces, ownership, setup, and unfinished work. Distinguish planned features and prior test results from behavior verified in the current chat. The user's current instructions take precedence.
- Re-read relevant documents after a context reset if their contents are no longer available. Update affected docs whenever implementation, setup, or contract behavior changes so the next chat receives accurate context.

## Project

36-hour hackathon project (DubHacks 2026). Audio-first AI companion for blind and low-vision people. A phone camera (head/chest mounted) does:
1. **Memory:** about once a second, find watched items (keys, wallet, phone, glasses, medication) and save each sighting with room, spot ("on the desk, next to the lamp") and time. Voice query "Where's my wallet?" -> "In your bedroom, on the desk, next to the lamp, about 4 minutes ago."
2. **Guidance:** back in the room, beeps pan left/right toward the item and speed up as it gets closer, plus clock-face cues ("Wallet at 2 o'clock", "Reach out now"); a closed loop until the user reaches the item.

Differentiator vs Ray-Ban Meta / Google Astra: persistent memory over hours + active steering, designed blind-first. Privacy: store sightings (item, place, time, small thumbnail), never video; frames are discarded after processing. Out of scope: glasses hardware, face recognition, outdoor navigation.

Tracks: Community Kitchen (main), Perceptron (our vision API), nebulaONE (a Purple agent, built in Purple's UI; only text files live in `purple/`), SideShift, DubHacks Next.

## Stack
- Backend: Python 3.11+, FastAPI, uvicorn, SQLite (stdlib), pydantic, python-dotenv, pytest. In `backend/`.
- Frontend: React + Vite (JavaScript), plain CSS, no UI framework. In `web/`.
- Browser APIs: getUserMedia, Web Audio (StereoPannerNode), SpeechSynthesis, SpeechRecognition, DeviceOrientation.
- Vision: Perceptron SDK pinned to `perceptron==0.4.0`; key `PERCEPTRON_API_KEY`; default model `perceptron-mk1.5`. Treat `isaac-0.3-fast` as an unverified candidate until the account model list and a real structured-output request confirm support. PyAV (`av>=16.1,<17`) decodes recorded videos.
- Development supports macOS/Linux and Windows/PowerShell; use commands matching the current environment. Install backend dependencies from `backend/requirements.txt`. Phone camera needs HTTPS: one `ngrok http 5173` (Vite proxies /api and /ws to :8000).

## Vision implementation status
- The real adapter, structured labels, optional item reference photos, video importer, and benchmark are implemented in `backend/app/vision/`. Both API routes use the same adapter; retain the package exports in `vision/__init__.py`.
- Default mode is still mock. The latest automatic place assignment implementation passed 150 backend tests without provider calls. A first real 7.82-second clip produced eight successful Mk1.5 responses and five sightings; median inference was 469.9 ms. Authentication and structured-output acceptance work for that model/clip. Broader accuracy, live auto-place evaluation, model comparisons, and phone latency remain unverified; do not present mock timings as real results.
- Real detections use `confidence: null`, with corresponding API types and a migration preserving existing SQLite sightings. Never invent a confidence score.
- Frames are prepared in memory via `backend/app/frames.py`. Provider boxes use 0..1000 internally and 0..1 in the app. Invalid uploads return 422/413; provider failures return 503 instead of a successful empty detection list.
- Video import uses a supplied room and recording start time plus frame timestamps. It saves sightings and thumbnail crops, not extracted full frames, and does not publish live dashboard events. It currently adds duplicates on re-import; dedupe/throttling remains unfinished.
- See `docs/VISION.md` for exact commands, configuration, reference-photo manifests, and remaining validation.

## Location memory status
- `backend/app/memory/` (`store.py`, `models.py`, `api.py`, `cli.py`, `observation.py`) and `backend/app/vision/places.py` support taught and automatically discovered SQLite places, text landmarks, directed connections, and per-sighting automatic or manual assignments. See `docs/MEMORY.md`.
- Sightings include stable `id`, nullable `place_id`, `place_source` (`"none"|"manual"|"automatic"`), and `place_reason`. `/api/where` includes `destination` from the newest sighting only. Never carry forward an older destination over a newer unassigned observation or treat profile `home_spot` as evidence.
- By default (`auto_place=true`), `/api/observe` and `vision.cli video` infer per-object resting places and open-vocabulary everyday objects (prioritizing watchlist items plus cups, water bottles, mugs, remotes, bags, canes, etc.) in one structured request (`vision.observe`), matching a candidate place or creating an `auto_<hex>` place when at least two distinct fixed landmarks are visible, and leaving ambiguous/carried items with `place_id: null` (`place_source: "none"`). `room` is optional (`"unknown room"` fallback). `/api/where` and `/api/guide` also accept any open-vocabulary object on demand.
- Optional explicit `place_id` on observation/video ingestion bypasses automatic place inference and assigns every detected object to that taught place (`place_source: "manual"`). Use `--no-auto-places` or `auto_place=false` to disable automatic place assignment.
- Camera localization (`vision.localize`) and directed BFS routing (`memory.plan_route`) are implemented and wired into `/api/guide` (`mode: visual|navigate|lost`). Facing/obstacle checks and ElevenLabs are not. Mock: `X-Mock-Hide` + `X-Mock-Place` force the navigate path without a provider key.
- Use the user's existing root `venv/`; do not create `backend/.venv`. Memory CLIs need no provider calls. Keep mock and real databases separate.

## Rules
- `docs/CONTRACT.md` is the API contract. Keep field names EXACTLY; update the doc when behavior changes.
- ALL Perceptron calls live in one class in `backend/app/vision/vision.py`. Default is `MockVision` (`PERCEPTRON_MODE=mock`); the repo must always run with no keys. Do not guess SDK signatures: verify against the PyPI page/docs or leave a TODO.
- `backend/app/guidance.py` stays pure (no I/O, no clock reads); every function has tests in `backend/tests/test_guidance.py`.
- The frontend never calls Perceptron. Never commit `.env` or keys. Never log frames or full keys. Frames are never written to disk (only thumbnail crops).
- Accessibility on the phone page: large touch targets, ARIA labels, aria-live status, nothing visual-only.
- Keep functions small, short docstrings, no framework sprawl, no unrequested features.
- Ownership: A vision/memory, B guidance/audio, C app/dashboard, D Purple/product. See `docs/ROLES.md`; follow `TODO(A|B|C|D)` markers.

## Change summaries
- After each completed set of project changes, create or update `SUMMARY.md` in the repository root before your final response, unless the user explicitly limits the files you may edit.
- Write for the user in clear, plain language. Cover every change you made, grouping related files where helpful: what changed, why, and what it means for the user's project or workflow.
- Include affected files, new or changed behavior, setup or usage instructions when relevant, and any compatibility changes, limitations, or remaining work. Clearly identify anything the user needs to do next.
- Report the checks actually performed and their results. Distinguish tested behavior from assumptions and anything still unverified; never claim an unrun test passed.
- Keep the summary current, distinguish your changes from pre-existing work, and never include credentials or secrets. Updating `SUMMARY.md` itself does not require another summary.

## Commands (PowerShell, from repo root)
- Backend: `cd backend; ..\venv\Scripts\python -m uvicorn app.main:app --reload --port 8000`
- Tests: `cd backend; ..\venv\Scripts\python -m pytest`
- Frontend: `cd web; npm run dev` (build check: `npm run build`)

## Commands (macOS/Linux, from repo root)
- Backend: `cd backend && ../venv/bin/python -m uvicorn app.main:app --reload --port 8000`
- Tests: `cd backend && ../venv/bin/python -m pytest -q`
- Vision tools: `cd backend && ../venv/bin/python -m app.vision.cli --help`
- Memory tools: `cd backend && ../venv/bin/python -m app.memory.cli --help`
- Frontend: `cd web && npm run dev` (build check: `npm run build`)
