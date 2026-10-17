# Sightline change summary

## Latest update: `backend/app/memory/` package, open-vocabulary object memory & on-demand guidance, and automatic per-object place assignment

Sightline now organizes all location memory modules inside `backend/app/memory/` (matching
`backend/app/vision/`), supports **open-vocabulary object memory and on-demand guidance** for
any everyday object a blind user might look for (such as a cup, water bottle, mug, remote,
bag, cane, or chapstick—not just the 5 default watchlist items), and automatically assigns
observed objects to persistent places during `/api/observe` and recorded-video import.

## What changed

- **`backend/app/memory/` (`__init__.py`, `__main__.py`, `store.py`, `models.py`, `observation.py`, `api.py`, `cli.py`):**
  moved `memory.py`, `memory_models.py`, `observation.py`, `memory_api.py`, and `memory_cli.py`
  into the `backend/app/memory/` package while preserving `from app import memory` exports.
  Run the memory CLI with `python -m app.memory.cli` (or `python -m app.memory`).
- **Open-vocabulary observation & on-demand guidance (`backend/app/db.py`, `vision/places.py`, `vision/vision.py`, `main.py`, `memory/store.py`):**
  - `PerceptronVision.observe()` no longer restricts `_LocatedLabel.item` to a closed 5-item
    JSON schema enum. Instead, it detects all visible watchlist items AND any other distinct
    everyday personal or household objects resting on surfaces (`cup`, `water bottle`, `mug`,
    `remote`, `headphones`, `bag`, `cane`, `laptop`, `charger`, `book`, `watch`, `hat`, etc.).
  - `db.canonicalize_item()`, `db.resolve_item()`, `db.target_item()`, and `db.recent_sightings()`
    normalize labels and match across common synonyms (`cup`/`mug`/`coffee cup`/`tumbler`,
    `water bottle`/`bottle`/`thermos`, `bag`/`backpack`/`purse`, `headphones`/`earbuds`/`airpods`,
    `cane`/`white cane`, etc.) as well as arbitrary observed item names in SQLite.
  - `/api/where` and `memory.cli where` resolve any watched or previously observed object (or
    reply `"I haven't seen your <item> yet."` when unseen instead of rejecting with `"not watching"`).
  - `/api/guide` can now actively locate and steer the user toward **any** requested object on
    demand, even if it was never added to `/api/profile`.
  - `/api/memory/items` (`memory.recalled_items()`) returns all configured watchlist items plus
    any additional open-vocabulary objects seen in `sightings`.
- **Automatic per-object place discovery (`backend/app/memory/observation.py`, `vision/places.py`):**
  per-object place resolution enforces at least two distinct fixed landmarks, groups items on
  the same physical surface via a frame-local `local_key`, rejects conflicting claims within a
  frame or stale catalog changes during inference, bounds candidate context to 40 places plus
  8 recent session-scoped observations (within 20 seconds), and commits new `auto_<hex>`
  places, sightings, and crops atomically.
- **`web/src/phone/Phone.jsx`, `web/src/phone/api.js`, and `web/src/shared/types.js`:** phone
  room selector defaults to `Automatic` (`""`) and sends a per-session `session_id` to
  `/api/observe`; updated JSDoc types with `place_source` and `place_reason`.
- **Tests (`backend/tests/test_auto_places.py`, `test_vision.py`, `test_video.py`, `test_api.py`, `test_memory.py`):**
  added coverage for open-vocabulary object observation (`"a coffee mug"` -> `"cup"`,
  `"water bottle"`, `"chapstick"`), synonym recall (`"my mug"`, `"my bottle"`), on-demand
  guidance, automatic place discovery/matching, and the `app.memory` package layout.

## Important behavior for your project

- **Any everyday object works:** You do not need to pre-register `"cup"` or `"water bottle"`
  in `/api/profile`. During `/api/observe` or video import, Perceptron can report any visible
  everyday object on surfaces and link it to an automatic or taught place. Asking
  *"Where's my water bottle?"*, *"Where's my mug?"*, or *"Find my cup"* works out of the box.
- **Automatic by default:** `/api/observe` and `python -m app.vision.cli video` infer places
  automatically unless `auto_place=false` / `--no-auto-places` is set or an explicit `place_id`
  override is supplied.
- **Conservative & per-object:** Different objects in the same frame can be assigned to
  different surfaces (`local_key`). If an object is carried, obscured, or lacks at least two
  distinct fixed landmarks, the object sighting is still saved with `place_id: null` and
  `place_source: "none"`.
- **Newest sighting wins:** `/api/where` returns `destination` from the newest sighting only.

## Verification completed

- **152 backend tests passed** (`../venv/bin/python -m pytest -q` from `backend/`).
- No live Perceptron or ElevenLabs requests were made during this verification.

## Remaining work

Verify automatic place discovery/matching and open-vocabulary detections on live Perceptron
clips, recognize the user's current camera position and facing when no watched object is
visible, then implement route selection with live arrival/obstacle checks and spoken ElevenLabs
cues. Sighting deduplication/throttling and broader 20-frame real-prop latency benchmarks also
remain pending.

## Earlier verified vision work

The Perceptron adapter, reference-photo support, video sampling, and benchmark tools already
existed before this update. The first real `room_scan_1.MOV` run previously produced eight
successful Mk1.5 responses and five sightings (phone, wallet, keys) in 7.82 seconds; median
inference was 469.9 ms and saved crops were visually checked. That small trial is not a
navigation test or a general accuracy/latency benchmark. See [VISION.md](docs/VISION.md).
