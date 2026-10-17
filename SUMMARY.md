# Sightline change summary

## Latest update: first real video test

Ran `backend/test_videos/room_scan_1.MOV` through the existing video command using the
configured `perceptron-mk1.5` model and API key. No application code was changed.
The clip is 7.82 seconds long; sampling at one frame per second produced eight successful
real model responses, with no errors. Used the embedded recording start time
`2026-10-17T21:00:24Z` and a supplied room label of `test_room` (not a model-inferred room).

- At 0–4 seconds: no watched objects were returned.
- At about 5 seconds: phone, “on the table.”
- At about 6 seconds: wallet and phone, “on the counter.”
- At about 7 seconds: keys and wallet, “on the counter.”
- Five sightings were saved to the configured real-results database, along with five crops.
  All five crops were visually inspected and appear consistent with their object labels.
  The surrounding location descriptions were not independently verified from full frames.
- Median inference time was 469.9 ms; the first call took 1214.9 ms. These are eight clip
  requests, not a 20-frame benchmark or a measurement of the phone/audio loop.

Full per-frame JSON output: [room_scan_1.results.jsonl](backend/test_videos/room_scan_1.results.jsonl).
The test verifies live authentication, this model's acceptance of the structured request, and
successful label parsing/storage on this clip. Broader accuracy, other models, performance,
and live guidance still need testing. Backend unit tests were not rerun for this test-only task.
Updated the documentation status to record this first live result; no key or full frames were
written into the output or documentation.

## Earlier update: documentation and new-chat context

Your project now has explicit instructions for each new chat to read the project documentation
before planning or editing. The docs also reflect the vision pipeline that was implemented
earlier in this conversation, so the next chat should not mistake it for an unfinished stub.
This update changes documentation only; it does not change application behavior.

- **`AGENTS.md`:** added a required reading sequence for the root README, this summary, and all
  Markdown files under `docs/`. The agent must read file contents, discover new documents, and
  report missing context. Added current vision status, compatibility notes, the SDK pin,
  unverified model caveats, and macOS/Linux commands alongside Windows commands. Preserved
  the requirement to write a user-facing summary after changes.
- **`docs/README.md`:** added a documentation index explaining what to read, where information
  belongs, and which claims remain unverified. The reading rule is a repository instruction;
  it does not change app settings or automatically attach file contents to every chat.
- **`docs/PROJECT_OVERVIEW.md`:** removed the stale claim that the real client is unfinished and
  the outdated 48-test status. Documented implemented features, the previous 89-test result,
  nullable confidence, error behavior, and the remaining live demo checks.
- **`docs/ROLES.md`:** distinguished completed implementation from real-world validation,
  updated the next steps for role A, and added interface notes for guidance/app teammates.
  Model comparisons now depend on account availability and request compatibility.
- **`docs/CONTRACT.md`:** made nullable confidence explicit in the record sketch and corrected
  the abbreviated `/api/where` response to match its existing HTTP 200 behavior. This corrects
  the documentation; it does not introduce a new endpoint behavior.
- **`docs/VISION.md`:** connected the guide to the documentation index, overview, contract,
  roles, and summary, and clearly labeled the 89-test result as prior verification.

## Earlier implementation already present before this update

These changes were made earlier in this conversation and are included here because this is
the first summary file. They were not reimplemented in this documentation update.

- **`backend/app/vision/vision.py` and `vision/__init__.py`:** implemented the real Perceptron
  adapter in the `vision/` layout that was already moved during the earlier task. Added
  structured prompts, optional cropped reference photos, account model discovery, validation,
  conversion from 0–1000 to 0–1 coordinates, and connection cleanup. Existing imports continue
  to work through the package exports. Mock mode remains the default.
- **`backend/app/frames.py` and `main.py`:** added in-memory image preparation shared by the
  inference/crop/dashboard flow, upload limits, UTC timestamp normalization, and clear error
  responses. Provider failures return 503; they do not masquerade as “nothing was found.”
- **`backend/app/models.py`, `db.py`, and `web/src/shared/types.js`:** allow `confidence: null`
  for real detections because no calibrated score is available. Existing SQLite sightings are
  preserved during the schema migration. Teammates should handle null as unknown, not zero.
- **`backend/app/vision/video.py` and `cli.py`:** added timestamp-aware video sampling,
  recorded-video ingestion into searchable memory, and median/p95 latency benchmarks.
  Full frames stay in memory; only object thumbnails are saved. Reference-photo and video
  commands are documented in the vision guide.
- **`backend/requirements.txt`, `backend/.venv`, and `.env.example`:** added/pinned the
  Perceptron SDK, added PyAV, installed a local backend environment, and documented model,
  timeout, frame-size, and reference-photo settings. No real API key was added.
- **`backend/tests/test_vision.py` and `test_video.py`:** added offline SDK, validation,
  API/memory, frame handling, video, benchmark, and migration tests.
- **`README.md`, `CLAUDE.md`, `AGENTS.md`, and `docs/`:** updated paths, setup guidance,
  implementation status, and the API contract. `CLAUDE.md` and the root README were not
  edited again in the latest documentation update.

## Validation

- Earlier implementation: **89 backend tests passed** using offline SDK responses, mock
  detections, and a generated video. One dependency deprecation warning was reported.
- Latest documentation update: checked local Markdown links and referenced project paths,
  reviewed the documented behavior against the existing implementation, and checked whitespace
  with `git diff --check`. Backend tests were not rerun for these documentation-only changes.
- Still unverified: live Perceptron authentication, available models, structured-output
  acceptance, visual accuracy, service latency, and the phone's full audio-guidance loop.

## What this means for you

You can exercise the vision pipeline in mock mode now and use the commands in
[the vision guide](docs/VISION.md). New agents working in this repository are instructed to
load the documentation instead of relying on earlier chat history.

When you have an API key, add it locally to `.env`, set `PERCEPTRON_MODE=perceptron`, use a
separate database for real trials, and restart the backend. Then list account models, verify
one real request, and run the recorded-video and latency checks with your props.

Dedupe/throttling is still unfinished: importing a clip twice adds duplicate sightings.
Rooms remain user supplied, and the video importer does not stream dashboard events.
The real Layer 0 demonstration remains to be validated.
