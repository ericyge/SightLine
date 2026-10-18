# API contract (backend implements, frontend consumes; keep field names EXACTLY)

Read the implementation notes below together with the endpoint list. They define the current
behavior where the original scaffold sketch is abbreviated. Setup and video-tool commands
live in [VISION.md](VISION.md).

Stored records:
  item:     { name, aliases[], description }
  room:     { name }
  sighting: { id (integer), item, room, spot, box:[x1,y1,x2,y2] (0..1), confidence (number|null), ts (ISO), thumb (path), place_id (string|null), place_source ("none"|"manual"|"automatic"), place_reason (string) }
  place: { id, name, room, description, landmarks:[string...] }
  connection: { from_place_id, to_place_id, departure_view, instruction, arrival_view }

Endpoints:
  POST /api/observe   multipart: frame (JPEG), room (optional string), ts (optional ISO), place_id (optional taught place ID), auto_place (optional bool, default true), session_id (optional PlaceID string)
      -> { sightings: [sighting...] }   saves sightings found in this frame, pushes an event to /ws/events
  GET  /api/where?item=wallet&from_place=bedroom_door   from_place is optional
      -> { item, found:true, last: { room, spot, minutes_ago, thumb, confidence }, history: [ ... up to 5 ... ], destination: place|null, directions:[string...], say: "In your bedroom, on the desk, next to the lamp, about 4 minutes ago." }
      -> HTTP 200 { item, found:false, last:null, history:[], destination:null, directions:[], say:"I don't know where your wallet is yet. I haven't seen it." } if never seen
  GET  /api/memory/directions?item=wallet&from_place=bedroom_door   from_place is optional
      -> { item, found, status, current_place:place|null, destination:place|null, path:[place_id...], steps:[string...], hops:[{from_place_id,to_place_id,instruction,departure_view,arrival_view,inferred_reverse:false}...], say }
  GET  /api/memory/map -> { places:[place...], connections:[connection...] }
  POST /api/memory/map JSON: { places:[place...], connections:[connection...] }
      -> full merged map; omitted records retained, no deletions
  GET  /api/memory/items -> [{ item, last:sighting|null, destination:place|null }...] for current watchlist
  PUT  /api/memory/sightings/{sighting_id}/place JSON: { place_id:string|null }
      -> updated sighting; null clears only this link
  POST /api/guide     multipart: frame (JPEG), item (string), yaw_deg (optional float)
      -> { found, point:[x,y], box:[x1,y1,x2,y2], angle, clock, distance ("far"|"near"|"reach"), pan, say, beep_ms,
           mode ("visual"|"navigate"|"lost"), current_place (place_id|null), destination (place|null), next_instruction (string|null), departure_status ("aligned"|"left"|"right"|"blocked"|"unknown"|null) }
      visual e.g. { "found": true, "point": [0.71, 0.62], "box": [0.66,0.55,0.77,0.69], "angle": 13.7, "clock": 12,
        "distance": "near", "pan": 0.46, "say": "Wallet slightly right, almost in reach", "beep_ms": 350,
        "mode": "visual", "current_place": null, "destination": {...}|null, "next_instruction": null }
      navigate e.g. { "found": false, "point": null, "box": null, "angle": null, "clock": null, "distance": null,
        "pan": 0.0, "say": "Follow the hallway toward the kitchen entrance.", "beep_ms": 0,
        "mode": "navigate", "current_place": "bedroom_door", "destination": {...}, "next_instruction": "Follow the hallway toward the kitchen entrance." }
  POST /api/profile   JSON: { watchlist:[{name,description,home_spot}], rooms:[str], guidance_style:"clock_face"|"left_right"|"beeps_only" }
      -> { ok:true }  stores the watchlist/rooms/style that the Purple setup agent produces
  GET  /api/profile   -> current profile (default watchlist: keys, wallet, phone, glasses, medication; rooms: bedroom, kitchen, living room)
  WS   /ws/events     server pushes JSON events: {type:"sighting"|"guide"|"say", ...payload}

## Location memory semantics

`place_id` is the observed object's destination, not the camera position.
By default (`auto_place=true` and no explicit `place_id`), `/api/observe` and video import
ask the vision model for per-object resting-place evidence alongside detections in a single
structured request (`vision.observe`). Each detected object can either:
- match an existing place in scope when at least two supplied fixed landmarks are visibly
  matched and uniquely distinguish that candidate (`place_source: "automatic"`),
- create a new discovered place (`auto_<hex>`) when at least two distinct fixed landmarks,
  a name, and a description are visible and do not overlap two or more landmarks of an
  existing place (`place_source: "automatic"`), or
- remain unassigned (`place_id: null`, `place_source: "none"`, with an explanatory
  `place_reason`) if carried, obscured, ambiguous, or conflicting within the frame. Object
  detections are still saved when place evidence is unresolved.

Supplying an explicit `place_id` on `/observe` or video import bypasses automatic place
inference, applies that taught place to every detected item in the frame/clip
(`place_source: "manual"`), and fills `room` from the taught place when `room` is omitted.
Setting `auto_place=false` (or `--no-auto-places` on the CLI) disables automatic place
inference and saves unassigned sightings (`place_source: "none"`). Manual assignment or
clearing can also be done per sighting afterwards via `PUT /api/memory/sightings/{id}/place`
or `memory_cli assign`. Old databases migrate automatically: existing IDs, timestamps, boxes,
and thumbnails survive, existing `place_id` links get `place_source: "manual"`, and unlinked
sightings start with `place_id: null`, `place_source: "none"`, `place_reason: ""`.
Re-importing a video still adds duplicates.

`room` is optional on `/api/observe` and video import. When supplied, it limits candidate
places to that room and overrides any model-inferred room label. When omitted, the matched
place's room, the model-inferred room, or `"unknown room"` is used. `session_id` (matching
the `PlaceID` pattern) optionally scopes up to 8 recent observations from the last 20 seconds
as continuity hints for the model.

`destination` always comes from the newest sighting. If that sighting is unassigned, it is null
even if an older sighting had a place. Empty detections preserve last-seen memory. `found:true`
means seen previously, not currently visible or confirmed still there. Profile `home_spot`
never substitutes for an observation. Historical observations are sorted by timestamp instant,
with sighting ID breaking ties; new writes use UTC. The latest sighting ID is in `history[0].id`.

Places can be explicitly taught or automatically discovered from object sightings; connections
are saved by map teaching or the recorded-video walkthrough mapper. They are directed and do
not imply a reverse path. Return travel requires separately saved connections for that direction.
`departure_view` and `arrival_view` store the facing/view context for that direction.
Map updates are atomic merges, not replacements. A place ID cannot change rooms. Profile
updates retain places and sightings; only currently watched items are returned by item lookup.
`/api/guide` localizes the camera against taught places and plans a directed BFS route when the
target item is not visible and the newest sighting has a destination. Facing is checked against the next recorded departure view before speaking a walking instruction.
Continuous obstacle avoidance is not implemented. Speech support is described in the text-to-speech section below.

`/api/memory/directions` and CLI `directions` return `status` of `ok`, `already_there`,
`item_not_seen`, `unknown_destination`, `unknown_current`, or `no_path`. `found` means a
sighting exists, not that a route exists. Only the newest sighting's saved `place_id` supplies
the destination: neither an older assignment, a place name in `spot`, a generic surface name,
nor a sole mapped place in the room substitutes for it. `from_place` must identify a known
place to get directions; omitted, empty, or unknown IDs yield `unknown_current` when the
destination is known. Map order never establishes the user's location. These read-only
endpoints do not perform camera localization; `/api/guide` does.

`steps` and `hops` are empty unless status is `ok` or `already_there` (the latter has an
arrival step but no hops). `path` is empty for unknown places or an unseen item, and contains
only the starting ID for `no_path`. `/api/where.directions` uses the same rules and is empty
without a known start and a mapped destination/path. Its `say` remains a last-seen description.
The existing `inferred_reverse` hop field is retained and is always false; instructions are
never generated by swapping left/right or assuming the user should turn around.

Validation: place IDs match `[a-z0-9][a-z0-9_-]{0,79}`; names/rooms/landmarks are nonblank,
trimmed strings up to 160 characters; descriptions/view context/instructions are nonblank,
trimmed strings up to 1000 characters. A place accepts at most 20 landmarks (defaults empty).
Each import accepts up to 100 places and 500 connections (both default empty), with no duplicate
IDs/edges, self-connections, or unknown endpoints. Extra fields in map/assignment JSON are rejected.
Unknown referenced place or sighting: 404. Room mismatch or attempting to move a stable place ID
to another room: 409. Malformed JSON/model validation: 422. Errors use `{detail: ...}`.
Room matching ignores case and surrounding whitespace. Invalid observation assignments are
rejected before provider calls or saved crops. Memory edits do not publish WebSocket events.

See [MEMORY.md](MEMORY.md) for a complete mock test and assignment of earlier real sightings.

## Guidance math (implement in guidance.py as pure, unit-tested functions)
- angle_deg(x, fov_h) = (x - 0.5) * fov_h        (x in 0..1, left to right; FOV default 65 from env FOV_H_DEGREES)
- clock_hour(angle) = round(angle / 30) mod 12, with 0 shown as 12   (0 deg = 12 o'clock, +30 = 1, +60 = 2, -30 = 11)
- distance_bucket(box, center_y): h = y2 - y1; h < 0.10 -> "far"; 0.10 <= h <= 0.30 -> "near"; h > 0.30 and center_y > 0.5 -> "reach"; otherwise "near"
- pan(angle) = clamp(angle / 30, -1, 1)
- beep_ms(distance) = 800 far, 350 near, 150 reach
- pitch_boost(angle) = true when abs(angle) <= 8 degrees
- smooth(prev_angle, new_angle) = 0.5*new + 0.5*prev (moving average, newest weight 0.5)
- yaw compensation: between vision updates, corrected_angle = last_angle - (current_yaw - yaw_at_last_vision_update)
- say(item, angle, clock, distance, style): short cues (< 8 words), speak a direction only when it changes; "reach out now" when distance == "reach"; if not found for 2 s: "Lost it. Turn slowly left." or "...right." (the shorter way back to where it was last seen, see below); style clock_face uses "Wallet at 2 o'clock", left_right uses "slightly left / turn right / straight ahead", beeps_only returns an empty say.

---

# Scaffold notes (how the code resolves details the contract leaves open)

The endpoint sketch originated in the project brief and is maintained to match the implementation.
The notes below specify additional behavior; update both when behavior changes.

- **Vision implementation:** `backend/app/vision/vision.py`, re-exported through the `vision` package.
- **`confidence` is nullable:** real Perceptron labels use `null` because no calibrated score is available. Mock scores remain numeric. SQLite automatically migrates older non-null columns while preserving existing sightings.
- **Frame preparation:** `/api/observe` and `/api/guide` decode and orient the uploaded image, strip metadata, and resize proportionally to `VISION_FRAME_MAX_PX` (default 640). All boxes, live dashboard frames, and saved crops use this same orientation. JPEG, PNG, and WebP are accepted; invalid images return 422 and uploads over 8 MiB return 413.
- **Vision failures:** HTTP 503 `{detail: ...}` for provider errors, timeouts, incomplete replies, or invalid labels. No sightings are saved for a failed frame. A successful empty list means no current detection, and never deletes previous sightings. The client never falls back to mock on an error.
- **Timestamps:** new `/api/observe` timestamps are normalized to UTC; invalid ISO timestamps return 422. The video importer requires a recording start time with a zone and adds each frame's presentation offset. Existing naive API timestamps are still treated as UTC.

- **`/api/where` never returns HTTP 404.** Never-seen is `200 {item, found:false, last:null, history:[], destination:null, say:"I don't know where your wallet is yet. I haven't seen it."}` for both profile watchlist items and open-vocabulary objects (e.g. `"water bottle"`, `"cup"`). Found responses include `found:true`.
- **`/api/where` `say` wording:** unassigned sightings retain `"In your bedroom, on the desk, next to the lamp, about 4 minutes ago."` (room + spot), or `"Last seen on the desk, next to the lamp, about 4 minutes ago."` when `room` is `"unknown room"`. Assigned sightings use `"Last seen at counter beside the sink in your kitchen, about 4 minutes ago."` (omitting `"in your unknown room"` when the place's room is `"unknown room"`). `history` entries are full sighting records, newest first. `minutes_ago` is a float.
- **`item` matching & open-vocabulary:** names, watchlist aliases, and common everyday object synonyms (`cup`/`mug`/`coffee cup`/`tumbler`, `water bottle`/`bottle`/`thermos`, `bag`/`backpack`/`purse`, `headphones`/`earbuds`/`airpods`, `cane`/`white cane`, etc.) are case-insensitive; `"my"`/`"the"`/`"a"`/`"an"` prefixes and a trailing plural `"s"` are ignored (`"my meds"` -> `medication`, `"my mug"` -> `cup`, `"my bottle"` -> `water bottle`). `/api/observe` detects both watchlist items and any other visible everyday personal/household objects on surfaces, and `/api/guide` can guide to any item on demand.
- **`thumb`** is a URL path served by the backend: `/api/thumbs/<id>.jpg` (96 px max crop around the box). It is `""` if the frame could not be decoded.
- **`/api/guide` modes:**
  - **visual:** target item is in the current frame. Existing pan/clock/beep guidance; `mode:"visual"`. `destination` is filled from the newest sighting when assigned. `current_place` and `next_instruction` are null (localization is skipped to save a provider call).
  - **navigate:** item not visible, destination known, current place localized, and a directed path exists (or the user is already at the destination). `found:false`, geometry null, `beep_ms` 0. `say` / `next_instruction` use the next taught connection instruction only after facing is aligned; otherwise `say` asks for an in-place scan/turn or a stop and `next_instruction` is null. Speak when the hop or facing status changes. At the destination without a visible item: `say` like `"You're at counter beside the sink. Look around slowly."` with `next_instruction` null.
  - **lost:** cannot offer visual or navigate cues. An unassigned newest sighting gives `"I last saw your wallet, but couldn't identify its place. Please slowly scan your surroundings."` once per sighting/session, with `destination` and `next_instruction` null. Failed localization asks the user to slowly scan, with the remembered destination retained. A missing directed path reports that no path is known. No walking instruction or beeps are returned for these cases; a later resolved route speaks its next cue again.
- **`/api/guide` when not found (visual-lost, no destination / no saved sightings):** `found:false`, `point/box/angle/clock/distance` are `null`, `pan` 0, `beep_ms` 0 (silence). `say` is `""` until the item has been missing for 2 s, then `"Lost it. Turn slowly left."` or `"Lost it. Turn slowly right."` once. **Which way:** the shorter turn back to where the item was last seen. The server remembers that sighting's angle and the phone's heading then (`yaw_deg`); when lost it corrects the angle for how far the phone has turned since (heading difference wrapped across 0/360), so a user who turned right past the item is told "left". With no heading it uses the side the item was last seen on; if it was never seen it says "left".
- **`/api/guide` `say`** is `""` when the direction or navigate hop has not changed since the last cue (server tracks this per item; a pause longer than 10 s starts a fresh session). The returned `angle` is smoothed against the previous angle if it was seen within 3 s.
- **`yaw_deg`** is accepted but not used server-side; yaw compensation happens in the browser (`web/src/phone/orientation.js`). Compensation wraps the yaw difference across 0/360.
- **`clock_hour`** rounds halves up (`floor(x + 0.5)`) instead of Python's banker's `round()`, so 15 deg -> 1 and -15 deg -> 12.
- **`distance_bucket`** rounds the box height to 4 decimals before comparing, so float noise (`0.6 - 0.5 = 0.0999...`) cannot flip a boundary.
- **Extra endpoint:** `GET /api/health -> {ok, mode, mock}` so UIs can show a MOCK badge.
- **WebSocket events** all carry `mock: bool`:
  - `{type:"sighting", sightings:[sighting...], frame: <data URL | null>}` one per `/api/observe` call
  - `{type:"guide", item, frame, found, point, box, angle, clock, distance, pan, say, beep_ms, mode, current_place, destination, next_instruction}` one per `/api/guide` call
  - `{type:"say", text, source:"where"|"guide"}` whenever the app has something to speak
  - `frame` is forwarded live to the dashboard only (omitted when over 200 KB) and never stored.
- **Mock hints:** header `X-Mock-Item-X: 0.8` (or query `?mock_x=0.8`) forces the detected item's center x; `X-Mock-Item-H` / `?mock_h=0.4` forces box height (0.4 at the default y gives `"reach"`). `X-Mock-Hide: 1` / `?mock_hide=1` forces no detection so navigate can run. `X-Mock-Place: bedroom_door` / `?mock_place=bedroom_door` forces mock localization (`unknown` forces null).

---

# Conversational layer (added after the original contract)

  POST /api/chat   JSON: { text, history:[{role:"user"|"assistant", content}] (last 6 used), current_room? }
      -> { intent:"find"|"where"|"stop"|"set_room"|"chitchat", item?, room?, say, provider }
  WS event: {type:"chat", user_text, intent, item, say, provider, mock}

- `text` is 1 to 500 characters. `provider` is whichever answered: `mock` (keyword matching, the default), `purple`, `gemini`, `openai_compat`, or `keyword_fallback` (every configured provider failed, timed out or had no key).
- **Provider order: purple -> gemini -> keyword.** The chain starts at `LLM_PROVIDER` and falls through the later providers on any error, timeout (3 s) or missing key; it always ends at keyword matching. `LLM_PROVIDER=purple` tries purple, gemini, keyword. `gemini` tries gemini, keyword. `mock` and `openai_compat` do not use gemini. A provider with no key configured is skipped without a request.
- **Privacy, all external providers:** only the user's transcribed text, the last 6 turns, and watchlist names, aliases and room names (plus the current room) are sent. Never camera frames, thumbnails, descriptions, home spots or sighting history. Keys, Authorization headers and request bodies are never logged.
- The backend asks a model (tool calling: `find_item`, `where_is_item`, `stop_guidance`, `set_room`) what the user wants. **`item` and `room` are validated against the stored profile** (names, aliases, rooms). An unknown room, or a missing item, becomes `intent:"chitchat"` with one short question in `say`. Item matching follows the rest of the app: it is open-vocabulary (an object off the watchlist is searched on demand), and the model is told to prefer watchlist names.
- **`say` for find / where / stop / set_room is written by the app, never the model.** For `where` it is exactly the `GET /api/where` answer built from stored sightings. Only `chitchat` speaks model text (collapsed whitespace, 200 characters max).
- `find` means "start guidance for `item`"; the phone then calls `/api/guide`. "I can't find my keys" maps to `find`, not `where`.
- `/api/chat` does not publish a separate `say` event (the `chat` event is the log entry). Chat text lives only in memory and the live event; nothing is written to the database.
- Config (see `.env.example`): `LLM_PROVIDER=mock|purple|gemini|openai_compat`; `PURPLE_API_BASE`, `PURPLE_API_TOKEN`, `PURPLE_MODEL`; `GEMINI_API_KEY`, `GEMINI_BASE_URL` (blank = Google's documented OpenAI-compatible endpoint), `GEMINI_MODEL`; `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`; optional `LLM_TIMEOUT_S` (default 3).
- The phone calls `/api/chat` first and falls back to its fixed-phrase matching if the request fails.

---

# Speech-to-text (added)

  POST /api/transcribe   multipart: audio (16 kHz mono WAV, up to 5 MB)
      -> { text, provider }            text may be "" for silence
  GET  /api/health       -> { ok, mode, mock, stt }     stt = "elevenlabs" | "browser"

- `STT_PROVIDER=browser` (default): `/api/transcribe` answers **503** and the phone uses the browser's built-in SpeechRecognition. `STT_PROVIDER=elevenlabs` with `ELEVENLABS_API_KEY` set: `health.stt` is `"elevenlabs"`, and the phone records a WAV while the button is held, uploads it, then sends the text to `/api/chat` exactly as browser STT would.
- Errors: **400** empty recording, **413** over 5 MB, **502** the provider failed (auth, quota, 429, timeout, bad response; no provider detail is returned). On any failure the phone says so and switches to browser recognition when the browser has it.
- Relay: `POST {ELEVENLABS_BASE_URL or https://api.elevenlabs.io/v1}/speech-to-text`, header `xi-api-key`, form fields `model_id` (`ELEVENLABS_STT_MODEL`, blank = `scribe_v2`), `tag_audio_events=false`, `diarize=false`, and `file`. No automatic retries (avoids double charges). Timeout `STT_TIMEOUT_S` (default 10).
- **Privacy:** audio is held in memory only, sent only to ElevenLabs, never written to disk, never logged, and not forwarded to the LLM (only the transcript is). The transcript is handled like any chat text.

---

# Live vision (added)

- `PERCEPTRON_MODE=mock` (default) or `live`. In live mode `/api/observe` and `/api/guide` call Perceptron (`perceptron` SDK, `detect`) with `classes` = each watched item's description (name if blank). Observe sends all items in one call; guide sends the one item.
- **Coordinates:** the SDK returns integers on a normalized 0-1000 grid (confirmed in the SDK source, `pointing/types.py`). The backend divides by 1000, clamps to 0..1 and orders the corners, so `box` is `[x1, y1, x2, y2]` in 0..1 as everywhere else. Degenerate boxes are ignored.
- **One call in flight.** If a frame arrives while Perceptron is still working on another, the endpoint answers **`204 No Content`** with no body and does nothing else (no sighting, no guide state change, no event). Clients treat 204 as "skip": keep the last result and keep the local beeps going. Frames are never queued.
- **Errors never fail a request.** A timeout (`PERCEPTRON_TIMEOUT_S`, default 2 s), 429, auth error, network error or unparseable reply counts as "not seen" for that frame: `/api/observe` returns no sightings, `/api/guide` returns `found:false` (and the usual "Lost it" cue after 2 s). One warning per minute is logged (error class only).
- **`confidence`** is a constant `0.8` in live mode: the SDK's box has no confidence field. **`spot`** is `""` in live mode (TODO: needs a second call), so `/api/where` answers "In your bedroom, about 4 minutes ago."
- `GET /api/health` also returns `vision` (`{calls, p50_ms, p95_ms, errors, dropped}` for the last 200 successful calls, `null` in mock) and `guide_max_fps` (env `GUIDE_MAX_FPS`, default 3, kept within 0.5-10). Clients send at most that many `/api/guide` frames per second, one request at a time. Memory (`/api/observe`) stays at about 1 frame per second.
- Frames are processed and discarded. Only the item, place, time and a small thumbnail are stored. `video_frames` and streaming endpoints are not used.

---

# Text-to-speech (added)

  POST /api/speak   JSON: { text }  (1 to 300 characters)   query: cached_only (bool, default false)
      -> 200 audio/mpeg (mp3), header X-TTS-Cache: hit | miss
      -> 204 (cached_only=true and the phrase is not cached; ElevenLabs is not called)
      -> 503 TTS is not enabled   502 ElevenLabs failed or timed out   422 bad text
  GET  /api/health  also returns  tts ("elevenlabs"|"browser")  and  tts_guidance ("cached"|"never")

- `TTS_PROVIDER=browser` (default): `/api/speak` answers 503 and phones use their built-in voice. `elevenlabs` needs `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID`; otherwise `health.tts` stays `"browser"`.
- Relay (per ElevenLabs' docs): `POST {ELEVENLABS_BASE_URL or https://api.elevenlabs.io/v1}/text-to-speech/{voice_id}?output_format=mp3_44100_128`, header `xi-api-key`, JSON body `{text, model_id}`. `model_id` = `ELEVENLABS_TTS_MODEL`, blank = `eleven_flash_v2_5` (unconfirmed on ElevenLabs' docs page; see `.env.example`). Timeout `TTS_TIMEOUT_S` (default 3). No automatic retries.
- **Cache:** text of 80 characters or less is cached in memory (LRU, 100 entries, keyed by a hash of voice + model + format + normalized text), so repeats are instant and free. The fixed guidance cues are pre-rendered in the background at startup.
- **Apps:** use `/api/speak` for chat replies (`say` from `/api/chat`). `tts_guidance` (env `TTS_GUIDANCE`) sets guidance cues: `"always"` (default) fetches every cue from `/api/speak` (1.5 s limit) so there is one voice; `"cached"` uses `cached_only=true` and a 204 means use the built-in voice; `"never"` always uses the built-in voice. Any error, 5xx or slowness (2.5 s for replies, 1.5 s for cues, 0.6 s for cached lookups) falls back to the built-in voice, so the user never hears nothing. **One speaker at a time:** starting any speech silences both voices first; a new reply interrupts anything; a guidance cue never interrupts a reply (it waits, and only the newest waiting cue is then spoken). Implemented in the Expo app (`mobile/src/voiceCore.ts`); the web page still uses the older cached-only behavior.
- **Privacy:** only the reply text goes to ElevenLabs. Never the user's audio, camera frames, thumbnails or history. The key and the spoken text are never logged.

---

# Wake word (added)

  WS  /ws/wake      client -> server: binary frames of 16 kHz mono 16-bit little-endian audio (under 64 KB each)
                    client -> server: text "reset" (clear the detector; send before resuming after a request)
                    server -> client: {"type":"ready"} | {"type":"wake","score":0.97} | {"type":"error","reason":"..."}
  GET /api/health   also returns  wake (true when /ws/wake can run)

- Opt-in: `WAKE_ENABLED=true`, the optional packages in `backend/requirements-wake.txt`, and the models from `python -m audio_directions.setup_wake` (never downloaded at runtime). Otherwise the socket answers `error` with the reason and closes, and `health.wake` is false.
- The phrase is "Hey Jarvis" (openWakeWord's pretrained stand-in; "Hey Sightline" would need a custom model). `WAKE_THRESHOLD` (default 0.6) sets the score needed. After a wake the server ignores further wakes for 3 s and resets its detector. At most 2 connections at once.
- **Privacy:** audio is scored in memory frame by frame and discarded. It is never stored, forwarded to any other service, or logged; only the wake event comes back. (It does travel from the phone to your backend, through the tunnel if you use one.)
- The phone starts the same listening flow as the talk button: it captures the request that follows on the same stream, stops when you stop talking, and sends it as a WAV to `/api/transcribe`, then `/api/chat`.

## Live apartment calibration

- `GET /api/calibration`: session/map status.
- `POST /api/calibration/start` (no body): start or resume without a starting place or room label.
- `POST /api/calibration/frame`: multipart `frame`, `session_id`, `sequence` (integer >= 1),
  `captured_at` (ISO); survey the place, then independently remember objects.
- `POST /api/calibration/finish`: JSON `{session_id}`; save even an incomplete map.

Responses share `{session_id:string|null, status:"idle"|"mapping"|"saved", available:bool,
mock:bool, frames_processed:int, last_sequence:int, current_place:place|null, places:[place...],
connections_total:int, rooms:[string...], missing_paths:[{from_place_id,to_place_id}...],
coverage_ready:bool, action:string|null, connection_added:bool, sightings:[sighting...],
memory_warning:string|null, say:string}`.

Start/continue retains the map, clears camera continuity, and recognizes the next view anew.
Session metadata persists in SQLite settings; the camera position and previous image do not.
One apartment, one camera, and one backend worker are supported. The phone has start/finish/
continue buttons and handles those phrases after transcription (before `/api/chat`). Mapping runs automatically while the native app is connected and not guiding; object queries
do not require manual finish. The browser has no controls yet.

Each frame uses the full Perceptron survey and then open-vocabulary observation, without a room
hint. Object destinations require independent evidence; camera position is never assigned to
all objects. `mock` and `perceptron` support calibration; detect-only `live` is refused with 503.
The object schema permits names beyond the watchlist and examples. The full observation prompt
returns empty results only when no relevant everyday objects are visible, not merely when no
watched objects are visible. Fast `live` memory and `auto_place=false` remain watchlist-limited.

The survey sends a recent previous image before the current image and now requires structured
`transition_observed`, `departure_view`, and `arrival_view`. A directed connection needs these,
a nonblank instruction, and uninterrupted camera continuity. No generic or reversed instruction
is fabricated. This also applies to recorded-video mapping. Matching needs two distinct,
unambiguous landmarks. Unknown views, sequence gaps, capture gaps over 10 seconds, resume/restart,
and survey errors break continuity; no connection is added across the gap. Prior map entries
remain without automatic revalidation. New survey places are refused at the 40-candidate limit.

Busy requests are rejected rather than queued (409). Unknown sessions return 404; finished
sessions, repeated/decreasing sequences or capture times return 409. Images follow standard
422/413 validation. Capture times older than 30 seconds or over 5 seconds in the future return
422. Sequences are reserved before inference, so failed attempts consume their number. Failed
surveys return 503. A successful survey followed by failed object inference returns 200 with
`memory_warning` and no new sightings; map progress is retained. The phone retries using a new
frame/sequence. Repeated progress cues stay quiet; errors and recovery are announced.

`coverage_ready` means at least two stored places have directed paths between every pair.
It does not establish apartment completeness, correct physical routes, facing, or obstacle
clearance. `missing_paths` lists unreachable ordered pairs, including return paths. Finish
reports incomplete coverage without deleting progress. `rooms` lists inferred labels excluding
`unknown room`, not physical room IDs.

A `calibration` WebSocket event carries frame results; saved objects also use existing sighting
events. No full images are persisted: one previous view is held in server RAM between requests,
replaced by new accepted frames and cleared on finish/resume/error. Phone upload timeouts are
40 seconds for calibration/guidance, 20 seconds for observation. Default calibration pacing is
at most one frame every two seconds, one request in flight.
See [CALIBRATION.md](CALIBRATION.md) for setup and limits.

## Departure facing check (latest checkpoint)

When an object is hidden and its destination is assigned, the backend attaches each candidate
place's next directed route departure to the existing localization request. Structured output
requires `departure_status: aligned|left|right|blocked|unknown` whenever departures are supplied.
The result must still identify a known place using at least two distinct landmark descriptions;
shared evidence that also fits another candidate is rejected. Missing/invalid structured
facing replies fail with 503, not presumed alignment. Plain localization CLI calls without
departures retain their previous schema. Real vision ignores mock facing hints.

`/api/guide` adds nullable `departure_status` (null for visual/lost/arrival). Navigate replies
only contain a walking `next_instruction` when it is `aligned`. Left/right cues request turning
in place; unknown asks for an in-place scan; blocked asks the user to stop. All these replies
have `found:false`, zero beeps, and null geometry. Each frame reassesses facing; the previous
alignment is not carried forward. Recovery repeats the saved instruction. This compares camera
facing to stored text, not body heading, physical clearance, or metric positioning.

`X-Mock-Facing` or `mock_facing` forces the mock assessment; omitted/invalid values mean unknown.
The native app announces camera/upload failures once, clears old visual beep state, and resumes
from fresh results. It does not speak responses from a cancelled guidance loop. The guide retains
its existing 40-second timeout; the check adds no provider call. Actual latency needs measurement.
See [NAVIGATION_TRIAL.md](NAVIGATION_TRIAL.md) for verification.

## Automatic background learning and unseen-object requests

The native app starts/resumes calibration on connection whenever the mode supports it, even
with an existing saved map. Normal place-recognition speech is suppressed; warnings and recovery
remain spoken. Manual controls are in the debug panel (hidden initially). Backend calibration
endpoints and persistence are unchanged. Learning means the foreground camera flow, not an OS
background-camera service when the app is closed or locked.

Commands briefly pause capture and wait for the current frame before querying memory. Recall
requests leave learning active. A real `find` response saves/finishes the current mapping session
before starting guidance. Stopping guidance resumes learning with fresh continuity. An explicit
manual finish remains an override until learning is restarted or the app reconnects.

For `find_item` with no saved sighting, `/api/chat` returns `intent:"where"` with the item and
“I don't know where your ITEM is yet. I haven't seen it.” It does not return `find` or launch
a camera search. This applies to watchlist objects, aliases, and open-vocabulary names, including
model tool calls and keyword fallback. `/api/where` uses the same message for never-seen items.
When a sighting exists, find can start; route/location/facing checks still determine whether
a walking instruction is possible. A saved sighting is not a claim the item is still there.
Native recall responses also end any previous guidance, so an unknown answer cannot leave the
user following directions to an earlier target. Low-level `/api/guide` remains available for
explicit camera detection tests; the conversational entry point enforces the unseen-item rule.
