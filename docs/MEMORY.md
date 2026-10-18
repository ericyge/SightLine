# Location memory

Sightline can now remember a stable destination for an object, such as `kitchen_counter`,
instead of relying only on changing model descriptions such as “on the counter.”
Places, landmarks, directed connections, sightings, and sighting-to-place links live in the
existing SQLite database selected by `DB_PATH`. They survive backend restarts.

Taught places and directed connections feed room-to-room navigation: `/api/guide` localizes
the camera against place landmarks (Perceptron structured call in `vision.localize`), plans a
BFS route over `place_connections` (`memory.plan_route`), and speaks the next stored
instruction until the target item is visible again (then existing beep/clock guidance).
Departure-facing checks now gate walking instructions; obstacle avoidance remains unfinished. ElevenLabs speech is implemented separately (see
`CONTRACT.md`). Memory CLI commands themselves do not call Perceptron.

## What is remembered

- **Place:** stable ID, readable name, room, description, and text landmarks. For example,
  `kitchen_counter`, “counter beside the sink,” with landmarks “sink” and “faucet.”
- **Connection:** a saved direction from map teaching or walkthrough mapping, with a departure
  view, instruction, and arrival view. The reverse direction is not invented. `/api/guide`
  speaks these instructions in order along the shortest directed path.
- **Sighting:** existing object, room, model-written spot, time, crop, box, and confidence,
  plus a stable numeric `id`, optional `place_id` linking the object to its destination,
  `place_source` (`"none"`, `"manual"`, or `"automatic"`), and `place_reason`.

An object being absent from a later frame does not erase its last sighting. A newer sighting
at an unassigned place returns `destination: null`; it does not revive an older mapped
destination. `found: true` means “has been seen,” not “visible now” or “still there.”
The profile's `home_spot` is a preference, not an observed location.
Directions follow the same newest-sighting rule. A generic surface description, even an exact
place name in `spot`, does not establish an assignment. If the latest sighting is unassigned,
confirm its actual place and assign it, or capture a new observation with sufficient landmarks.

Sightings can now be assigned to places **automatically** during `/api/observe` and video
ingestion (`auto_place=True` by default) or **manually** via `place_id` / `assign`. In a
single structured request (`vision.observe`), the model reports each object's resting surface
and at least two fixed landmarks, either matching a supplied candidate place or discovering a
new `auto_<hex>` place when unambiguous. Ambiguous, carried, or conflicting observations save
the object sighting with `place_id: null` and `place_source: "none"`. Landmarks remain text
only, without scene reference photos or world coordinates.

## Quick test using a separate mock database

Use the existing `Sightline/venv`; do not create a second environment inside `backend`.
From the Sightline repository root on macOS/Linux:

```bash
source venv/bin/activate
cd backend
export DB_PATH=sightline-memory-demo.db
export THUMBS_DIR=thumbs/memory-demo
export PERCEPTRON_MODE=mock
python -m app.memory.cli teach examples/two_room_memory.json
python -m app.memory.cli map
```

The example is a **fictional layout** with a bedroom doorway, kitchen entrance, and kitchen
counter. Replace its labels, landmarks, and connections with the actual demo space before
using it for real data. Import merges supplied places/connections; omitted records are kept.
Nothing is automatically imported at startup.

Exercise the complete path using your existing clip and **fake** detections:

```bash
python -m app.vision.cli video test_videos/room_scan_1.MOV --room kitchen --place-id kitchen_counter --started-at "2026-10-17T21:00:24Z" --limit 1 --mock
python -m app.memory.cli where wallet
python -m app.memory.cli items
```

This intentionally uses a fictional kitchen label to test plumbing, not the clip's real
location. `where` returns the latest five observations and the full `destination` object.
`items` returns every watched item, its latest sighting (or null), and destination (or null).
These commands run without starting a server. Restarting them uses the same saved database.

To check the HTTP response, start the backend **in that same terminal environment**:

```bash
python -m uvicorn app.main:app --reload --port 8000
```

Then open `http://localhost:8000/api/where?item=wallet` or use the interactive API at
`http://localhost:8000/docs`. A backend already running in another terminal may point at a
different database. Keep separate mock and real databases.

After the mock test, stop that server and run `unset DB_PATH THUMBS_DIR PERCEPTRON_MODE`
to return to your `.env` settings for subsequent commands. Do not overwrite your `.env`.
On PowerShell, activate `venv\Scripts\Activate.ps1` at the repo root, set the three values
with `$env:NAME='value'`, and clear those overrides with `Remove-Item Env:NAME` when finished.

## Connect the real sightings you already saved

Use `python -m app.memory.cli where wallet` from `backend/` without the mock test's environment
overrides. It reads the database configured in your existing `.env`. The first real clip used
the room label **`test_room`**. Its old sightings gain IDs and initially have `place_id: null`.
Migration retains their time, boxes, confidence, and thumbnail links.

1. Inspect the latest sighting and its crop to confirm the object's actual surface.
2. Create a map JSON with a place in that same recorded room, `test_room`, using an accurate
   name and landmarks. The shape is shown in `examples/two_room_memory.json`; connections
   can be omitted until you know the layout.
3. Import your map with `python -m app.memory.cli teach /absolute/path/my-map.json`.
4. Attach the chosen sighting using the ID returned by `where`:

```bash
python -m app.memory.cli assign SIGHTING_ID --place-id YOUR_PLACE_ID
python -m app.memory.cli where wallet
```

Replace the uppercase placeholders; `SIGHTING_ID` is a number. Assignment changes only that
sighting's link, not its timestamp or other sightings. No video reprocessing or paid inference
is needed. To undo a link: `python -m app.memory.cli assign SIGHTING_ID --clear`.
The historical `.results.jsonl` file is not rewritten; query SQLite through the CLI/API for
current IDs and assignments.

For future focused captures, supply optional `--place-id kitchen_counter` to the video command,
or multipart `place_id=kitchen_counter` to `/api/observe`. **This means every detected object
in that clip/frame is at that place (`place_source: "manual"`). It is not the camera's current
position.** For a scan of several surfaces, omit `place_id` so each detected object is
evaluated and assigned to its own surface automatically (`place_source: "automatic"`), or pass
`--no-auto-places` / `auto_place=false` to save unassigned sightings and assign individual
sightings afterwards. The phone UI defaults its room selector to `Automatic` (`""`) and sends
a `session_id` so automatic per-object place discovery and matching run by default.

## API and validation

- `GET /api/memory/map`: all saved places and directed connections.
- `POST /api/memory/map`: merge `{places: [...], connections: [...]}` and return the full map.
- `GET /api/memory/items`: latest sightings/destinations for the current watchlist.
- `PUT /api/memory/sightings/123/place`: `{ "place_id": "kitchen_counter" }` to link,
  or `{ "place_id": null }` to clear; returns the updated sighting.
- `GET /api/where?item=wallet`: destination (full place or null) and last-seen description;
  optional `from_place` adds `directions` when the starting place and directed route are known.
- `GET /api/memory/directions?item=wallet&from_place=bedroom_door`: route status, places, steps,
  and hops. Without a known starting ID, returns `unknown_current` when the destination is known.

IDs are 1–80 lowercase letters, digits, underscores, or hyphens, beginning with a letter/digit.
Unknown places/sightings return 404; room conflicts return 409; malformed requests return 422.
Room comparison ignores case and surrounding whitespace. A stable place ID cannot be moved
to another room; create a new ID for a different destination. Name/description/landmarks and
connection instructions can be updated. Duplicate IDs or edges within one import are rejected.
Every connection endpoint must already exist or be included in the same import. Imports are
atomic: an invalid entry leaves the existing map unchanged. There is no map deletion endpoint yet.

See [CONTRACT.md](CONTRACT.md) for exact fields. The memory routes do not broadcast WebSocket
events; reload the map/items/where response after edits. Updating the profile does not delete
places or historical sightings, but `items`/`where` only search watched items.

## Room-to-room navigation (mock test)

After teaching `examples/two_room_memory.json` and assigning a sighting to `kitchen_counter`:

```bash
# Force: item not in frame, camera at bedroom_door
curl -s -X POST http://localhost:8000/api/guide \
  -F frame=@/path/to/frame.jpg -F item=keys \
  -H "X-Mock-Hide: 1" -H "X-Mock-Place: bedroom_door" -H "X-Mock-Facing: aligned"
```

Debug localization alone: `python -m app.vision.cli localize frame.jpg --mock --mock-place bedroom_door`.

## Walkthrough mapping (preferred training; no hand-written JSON)

Build places and connections by walking with the camera:

```bash
export DB_PATH=sightline-map.db THUMBS_DIR=thumbs/map
python -m app.vision.cli mapwalk /path/to/walkthrough.MOV --mock   # omit --mock for Perceptron
python -m app.memory.cli map
```

Default sample rate is 0.5 fps. Reuse the same `DB_PATH` across clips to grow the map.
JSON `teach` remains an optional override, not the required training path.
Record and verify both directions for routes the demo will use. A recording from counter to
bedroom does not supply instructions from bedroom to counter. Model-generated connections still
need to be checked against the actual apartment and facing direction before a walking trial.

## Step 1 verification: no guessed routes

From `Sightline/backend`, run:

```bash
../venv/bin/python -m pytest tests/test_navigation_checkpoint.py -q -s
```

This runs 12 checks using a temporary fictional apartment, mock camera detections/localization,
and a fresh database per case. It prints spoken responses and leaves your real memory and keys
untouched. It exercises a supported route, arrival, final visual guidance, unknown starts,
ambiguous/unassigned destinations, cleared assignments, lost localization, and missing return
connections. It verifies backend decisions, not real camera accuracy or phone audio.
See [demo checkpoints](DEMO_CHECKPOINTS.md) for the expected results and next step.

To inspect your existing memory without modifying it:

```bash
../venv/bin/python -m app.memory.cli directions wallet
../venv/bin/python -m app.memory.cli map
```

The first command should not produce walking steps without a starting place. If the latest
wallet sighting has an assignment, expect `unknown_current`; otherwise expect
`unknown_destination` or `item_not_seen`. After confirming a starting ID from your actual map,
pass `--from-place YOUR_CONFIRMED_PLACE_ID`. This is an inspection tool; live guidance obtains
the starting place from the camera.

## Remaining work

Verify native calibration, mapwalk, and localization on live Perceptron apartment walks;
physical facing validation, obstacle avoidance, and sighting dedupe. Native automatic room/session handling and the live
calibration flow are implemented.
Full directions lists and ElevenLabs speech exist. The live apartment demo remains unverified.

## Live first-use calibration (latest update)

The native phone learns automatically while connected and not guiding; manual calibration controls
are retained in the debug panel. No starting ID or room
labels are needed: the camera discovers places and records supported directed transitions.
Automatic room assignment and observation session IDs are wired into the native app too.
See [CALIBRATION.md](CALIBRATION.md) for an isolated two-room trial. Full Perceptron calibration
retains open-vocabulary object memory. The scripted mock walk includes a wallet, cup, and
chapstick; those fixtures are not a whitelist for real inference.

Physical camera recognition, transition correctness, facing, and obstacle checks still need
verification. An explicit `from_place` remains only for tools querying routes without a camera.

See [NAVIGATION_TRIAL.md](NAVIGATION_TRIAL.md) for the implemented facing gate and its phone test.

Unseen objects now get an explicit unknown-location answer for both Where and Find requests.
Find does not start guidance until at least one sighting exists. See CALIBRATION.md for continuous
learning, query ordering, automatic navigation handoff, and resuming after Stop.
