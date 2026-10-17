# Sightline change summary

## Latest update: walkthrough mapping from video (no hand-written map JSON)

You can map an apartment by walking and recording. `python -m app.vision.cli mapwalk`
surveys each frame with Perceptron (or mock): match an existing place or create a new one
from fixed landmarks, and when the place changes, auto-add a directed connection with a
spoken instruction. No manual place/connection JSON is required for training.

## Previous update: room-to-room navigation (localize → BFS route → `/api/guide`)

Sightline can now guide a user from their current taught place to an item's last destination
without Gemini: Perceptron (or mock) localizes the camera, Python BFS picks the next connection
instruction, and when the item reappears the existing beep/clock visual guide takes over.

## What changed

- **`backend/app/vision/vision.py`:** `LocalizationResult`, `VisionClient.localize`,
  `MockVision.localize` (hint-driven), `PerceptronVision.localize` (structured place match;
  requires ≥2 landmarks), package helper `vision.localize`. Mock detect supports `hide`.
- **`backend/app/vision/cli.py`:** `python -m app.vision.cli localize <image> [--room ...]
  [--mock --mock-place ...]`.
- **`backend/app/memory/route.py`:** `plan_route` / `RoutePlan` / `RouteHop` — directed BFS over
  `place_connections` with statuses `ok`, `already_there`, `no_path`, `unknown_current`,
  `unknown_destination`.
- **`backend/app/main.py` + `models.py` + `guidance.py`:** `/api/guide` returns
  `mode` (`visual`|`navigate`|`lost`), `current_place`, `destination`, `next_instruction`.
  Navigate speaks stored connection instructions only when the hop changes. Mock headers
  `X-Mock-Hide` / `X-Mock-Place` (or query params) exercise the path without a key.
- **Tests:** `test_route.py`, `test_localize.py`, `test_navigate.py`, plus `navigate_say` unit test.
- **Docs/types:** `CONTRACT.md`, `MEMORY.md`, `VISION.md`, `AGENTS.md`, `ROLES.md`,
  `PROJECT_OVERVIEW.md`, `web/src/shared/types.js`.

## How to test (mock)

```bash
source venv/bin/activate
cd backend
export PERCEPTRON_MODE=mock DB_PATH=sightline-nav-demo.db THUMBS_DIR=thumbs/nav-demo
python -m app.memory.cli teach examples/two_room_memory.json
# Assign a keys sighting to kitchen_counter (observe+assign, or insert via API), then:
# X-Mock-Hide=1 X-Mock-Place=bedroom_door on POST /api/guide item=keys
python -m pytest -q
```

## Verification completed

- **166 backend tests passed** (`../venv/bin/python -m pytest -q` from `backend/`).
- No live Perceptron requests during this verification.

## Phone

No phone code changes required: Find continues to call `/api/guide` ~1/sec and speaks `say`.

## Remaining work

Live Perceptron verification of `localize` accuracy/latency, facing and obstacle checks,
ElevenLabs, sighting dedupe/throttling, and broader real-prop benchmarks.
