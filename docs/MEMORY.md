# Location memory

Sightline can now remember a stable destination for an object, such as `kitchen_counter`,
instead of relying only on changing model descriptions such as “on the counter.”
Places, landmarks, directed connections, sightings, and sighting-to-place links live in the
existing SQLite database selected by `DB_PATH`. They survive backend restarts.

This is the memory foundation for returning to an object. It does not yet recognize the
user's current place/facing, calculate a route, check obstacles, or give room-to-room
instructions. `/api/guide` still points toward an object visible in the current frame.
ElevenLabs has not been integrated. Memory commands themselves do not call Perceptron.

## What is remembered

- **Place:** stable ID, readable name, room, description, and text landmarks. For example,
  `kitchen_counter`, “counter beside the sink,” with landmarks “sink” and “faucet.”
- **Connection:** an explicitly taught direction from one place to another, with a departure
  view, instruction, and arrival view. The reverse direction is not invented. These are
  saved map annotations for future navigation, not instructions currently executed by the app.
- **Sighting:** existing object, room, model-written spot, time, crop, box, and confidence,
  plus a stable numeric `id`, optional `place_id` linking the object to its destination,
  `place_source` (`"none"`, `"manual"`, or `"automatic"`), and `place_reason`.

An object being absent from a later frame does not erase its last sighting. A newer sighting
at an unassigned place returns `destination: null`; it does not revive an older mapped
destination. `found: true` means “has been seen,” not “visible now” or “still there.”
The profile's `home_spot` is a preference, not an observed location.

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
- `GET /api/where?item=wallet`: existing response plus `destination` (full place or null).

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

## Remaining work

Verify automatic place discovery and landmark matching on live Perceptron clips, determine
the user's current camera position and facing when no watched object is in view, then
select routes with live arrival/obstacle checks and feed short confirmed cues to ElevenLabs.
The destination IDs, automatic/manual place assignments, and directed connections provide the
stored inputs for that work. Repeated video imports still add duplicate sightings; this change
does not add deduplication.
