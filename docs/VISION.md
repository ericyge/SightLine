# Vision and recorded-video workflow

Your entry point is `backend/app/vision/vision.py`. `PerceptronVision.detect()` takes one
JPEG plus the watchlist and returns `Detection` records. Both `/api/observe` and `/api/guide`
use it. All provider requests live in this class. The `vision/` package preserves existing
`from app.vision import ...` imports.

For overall status and team handoff, read [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md) and
[ROLES.md](ROLES.md). For the exact app-facing fields and errors, read [CONTRACT.md](CONTRACT.md).
The latest change explanation is in root [SUMMARY.md](../SUMMARY.md). New chats should start
with [the documentation index](README.md), as required by `AGENTS.md`.

## Run locally without a key

From the repository root, on macOS/Linux:

```bash
source venv/bin/activate
python -m pip install -r backend/requirements.txt
cd backend
export PERCEPTRON_MODE=mock
export DB_PATH=sightline-mock.db
export THUMBS_DIR=thumbs/mock
python -m pytest -q
python -m uvicorn app.main:app --reload --port 8000
```

The current checkout uses the existing root `venv`. On a fresh checkout only, create it with
`python3 -m venv venv`. On Windows, activate `venv\Scripts\Activate.ps1` from the repo root
and use `$env:NAME='value'` for the environment overrides. Do not create `backend/.venv`.
Mock labels do not reflect image contents. After testing, stop the mock server and unset these
three overrides before using `.env` for real trials (`unset PERCEPTRON_MODE DB_PATH THUMBS_DIR`
on macOS/Linux). A shell override takes precedence over `.env`.

## First milestone: one recorded room becomes memory

Record a short clip showing your wallet, keys, and phone on distinct surfaces. Use one room
per clip for now: `--room` is a supplied label, not a room inferred by the model. Stop the
server or use a separate database while importing. From `backend/`, with the environment active:

```bash
python -m app.vision.cli video /absolute/path/walkthrough.mp4 --room bedroom --started-at "2026-10-17T12:00:00-07:00" --mock
```

Replace the path and recording start time. `--started-at` requires a time zone. Actual video
presentation timestamps are added to it; model processing time never changes when the item
was seen. The default samples one frame per second, resized proportionally to at most 640 px.
Use `--fps 0.5`, `--size 512`, or `--limit 20` to change sampling or bound a trial.

Each output line is JSON with `mock`, video offset, capture time, inference time, and saved
sightings. `--mock` explicitly forces fake detections even if real mode is configured.
The command writes sightings to the same SQLite database used by the backend and only
saves small object crops. It reads your existing video without copying it or saving extracted
frames. Import is sequential and does not queue requests or publish live WebSocket events.
Do not mix mock and real evaluation data: use a fresh `DB_PATH`/`THUMBS_DIR` for real trials.
Importing the same video twice currently adds a second set of sightings.

Sightings now include a stable `id`, nullable `place_id`, `place_source` (`"none"`,
`"manual"`, or `"automatic"`), and `place_reason`. By default, `video` and `/api/observe`
run automatic per-object place assignment (`vision.observe`), matching each detected object
against candidate places in scope or creating a new `auto_<hex>` place when at least two
distinct fixed landmarks support it (`--room` is optional). Pass `--no-auto-places` (or
`auto_place=false`) to disable automatic place inference. Optional `--place-id` manually
assigns every detected object to a taught place in the supplied room, so use it only for a
focused capture of one surface. See [MEMORY.md](MEMORY.md) for saved places/landmarks,
manual assignment without reprocessing, and recall through `python -m app.memory.cli where wallet`.
No server is needed for that query.

Start the backend and query:

```bash
curl 'http://localhost:8000/api/where?item=wallet'
```

## Add your API key later

Copy `.env.example` to `.env` in the repository root, then edit it locally:

```dotenv
PERCEPTRON_MODE=perceptron
PERCEPTRON_API_KEY=your-key-here
PERCEPTRON_MODEL=perceptron-mk1.5
PERCEPTRON_TIMEOUT_SECONDS=15
VISION_FRAME_MAX_PX=640
DB_PATH=sightline-real.db
THUMBS_DIR=thumbs/real
```

Restart the backend after changing settings. `.env` is ignored by Git; never put the key in
frontend code. The frontend and video command share the adapter. Omit `--mock` to use the
mode from `.env`. Environment variables already set in your shell take precedence over `.env`.

From `backend/`, list the models available to your account:

```bash
python -m app.vision.cli models
```

Mk1.5 is the documented default. The plan's `isaac-0.3-fast` remains a candidate only if your
account lists it and it accepts these structured-output requests; there is no silent fallback
to another model or to mock detections.

## Measure latency

Use a clip with at least 20 sampled frames. Each requested model receives the same frames:

```bash
python -m app.vision.cli benchmark /absolute/path/walkthrough.mp4 --count 20 --task observe --models perceptron-mk1.5
python -m app.vision.cli benchmark /absolute/path/walkthrough.mp4 --count 20 --task guide --item wallet --models perceptron-mk1.5
```

Pass multiple IDs after `--models` to compare available models. Results report median and
nearest-rank p95 latency for successful calls, plus attempts, successes, and errors. Timing
includes SDK serialization, transport, inference, and label parsing; it excludes video decoding,
database writes, phone upload, and audio playback. It is not full phone-to-audio latency.
There is no warmup; the first connection is included. Failed calls are counted separately and
make the command exit with code 1. Nothing is saved to sighting memory by the benchmark.
`--mock` checks the benchmark plumbing only; its timings say nothing about Perceptron speed.

## Personal item reference photos

Optionally prepare 1–3 tight crops of each personal item. Put them in the ignored `uploads/`
directory and create, for example, `uploads/references.json`:

```json
{"wallet": ["wallet-front.jpg", "wallet-back.jpg"], "keys": ["keys.jpg"]}
```

Set `PERCEPTRON_REFERENCES` to the manifest's absolute path and restart the backend. Photo
paths are relative to the manifest. Only references for requested items are sent, before the
current frame. Prompts explicitly distinguish identity references from current observations.
Photos are sent inline; no provider Files API upload is used. More references add latency.
Matching quality still needs testing with your actual props.

## Labels and failure behavior

- One schema-constrained request returns item name, box, and a short landmark-relative spot.
  Guidance requests omit the descriptive spot to reduce output. The room stays user supplied.
- Provider coordinates are requested on a 0–1000 grid and converted to the contract's 0–1 grid.
  Invalid, inverted, nonfinite, out-of-range, unknown-item, and duplicate-item results fail validation.
- `confidence` is `null` for real labels: this pipeline has no calibrated confidence score.
  Mock mode retains its explicitly fake score. Existing databases migrate without discarding sightings.
- Empty successful results preserve previous memory. Timeouts, API errors, malformed JSON, and
  incomplete answers produce HTTP 503 and do not save this frame. Provider exception contents
  are not logged or forwarded. No automatic retry makes live frames wait behind stale work.
- Frames are decoded, oriented, resized, and stripped of metadata in memory. Model, dashboard,
  and crop coordinates refer to the same prepared image. Uploads over 8 MiB return 413; invalid
  images and invalid timestamps return 422.

The last backend test run during implementation passed **89 tests** without live Perceptron calls.
This is historical validation, not a claim that tests were rerun during later documentation edits.
Verified locally: SDK request construction via an offline HTTP transport, response validation,
memory/guidance endpoint integration, old-database migration, frame preparation, and generated
video ingestion. The first live clip run now confirms API authentication and Mk1.5 structured-output acceptance;
broader accuracy, account model comparisons, and measured benchmark latency remain pending. Dedupe/throttling remains a separate TODO
in `/api/observe`; camera-relative box changes alone do not prove an item moved.

SDK reference checked during implementation:
[Perceptron Python SDK](https://github.com/perceptron-ai-inc/perceptron) and
[official quickstart](https://docs.perceptron.inc/perceptron-mk1.5/index).

## First live clip result

`test_videos/room_scan_1.MOV` (7.82 seconds) was processed successfully by Mk1.5 at 1 fps:
eight real responses, five sightings, no API/validation errors. Phone appeared at about 5 seconds;
wallet and phone at 6 seconds; keys and wallet at 7 seconds. All five saved crops were visually
checked and appear consistent with their labels. Median inference was 469.9 ms, including a
1214.9 ms first call. This small trial does not establish general accuracy or phone guidance latency.
The supplied room was `test_room`; recording time came from the clip metadata. Full results:
[per-frame JSON](../backend/test_videos/room_scan_1.results.jsonl).
