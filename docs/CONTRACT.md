# API contract (backend implements, frontend consumes; keep field names EXACTLY)

Read the implementation notes below together with the endpoint list. They define the current
behavior where the original scaffold sketch is abbreviated. Setup and video-tool commands
live in [VISION.md](VISION.md).

Stored records:
  item:     { name, aliases[], description }
  room:     { name }
  sighting: { item, room, spot, box:[x1,y1,x2,y2] (0..1), confidence (number|null), ts (ISO), thumb (path) }

Endpoints:
  POST /api/observe   multipart: frame (JPEG), room (string), ts (optional ISO)
      -> { sightings: [sighting...] }   saves sightings found in this frame, pushes an event to /ws/events
  GET  /api/where?item=wallet
      -> { item, found:true, last: { room, spot, minutes_ago, thumb, confidence }, history: [ ... up to 5 ... ], say: "In your bedroom, on the desk, next to the lamp, about 4 minutes ago." }
      -> HTTP 200 { item, found:false, last:null, history:[], say:"I haven't seen your wallet yet." } if watched but never seen
  POST /api/guide     multipart: frame (JPEG), item (string), yaw_deg (optional float)
      -> { found, point:[x,y], box:[x1,y1,x2,y2], angle, clock, distance ("far"|"near"|"reach"), pan, say, beep_ms }
      e.g. { "found": true, "point": [0.71, 0.62], "box": [0.66,0.55,0.77,0.69], "angle": 13.7, "clock": 12, "distance": "near", "pan": 0.46, "say": "Wallet slightly right, almost in reach", "beep_ms": 350 }
  POST /api/profile   JSON: { watchlist:[{name,description,home_spot}], rooms:[str], guidance_style:"clock_face"|"left_right"|"beeps_only" }
      -> { ok:true }  stores the watchlist/rooms/style that the Purple setup agent produces
  GET  /api/profile   -> current profile (default watchlist: keys, wallet, phone, glasses, medication; rooms: bedroom, kitchen, living room)
  WS   /ws/events     server pushes JSON events: {type:"sighting"|"guide"|"say", ...payload}

## Guidance math (implement in guidance.py as pure, unit-tested functions)
- angle_deg(x, fov_h) = (x - 0.5) * fov_h        (x in 0..1, left to right; FOV default 65 from env FOV_H_DEGREES)
- clock_hour(angle) = round(angle / 30) mod 12, with 0 shown as 12   (0 deg = 12 o'clock, +30 = 1, +60 = 2, -30 = 11)
- distance_bucket(box, center_y): h = y2 - y1; h < 0.10 -> "far"; 0.10 <= h <= 0.30 -> "near"; h > 0.30 and center_y > 0.5 -> "reach"; otherwise "near"
- pan(angle) = clamp(angle / 30, -1, 1)
- beep_ms(distance) = 800 far, 350 near, 150 reach
- pitch_boost(angle) = true when abs(angle) <= 8 degrees
- smooth(prev_angle, new_angle) = 0.5*new + 0.5*prev (moving average, newest weight 0.5)
- yaw compensation: between vision updates, corrected_angle = last_angle - (current_yaw - yaw_at_last_vision_update)
- say(item, angle, clock, distance, style): short cues (< 8 words), speak a direction only when it changes; "reach out now" when distance == "reach"; if not found for 2 s: "Lost it. Turn slowly left."; style clock_face uses "Wallet at 2 o'clock", left_right uses "slightly left / turn right / straight ahead", beeps_only returns an empty say.

---

# Scaffold notes (how the code resolves details the contract leaves open)

The endpoint sketch originated in the project brief and is maintained to match the implementation.
The notes below specify additional behavior; update both when behavior changes.

- **Vision implementation:** `backend/app/vision/vision.py`, re-exported through the `vision` package.
- **`confidence` is nullable:** real Perceptron labels use `null` because no calibrated score is available. Mock scores remain numeric. SQLite automatically migrates older non-null columns while preserving existing sightings.
- **Frame preparation:** `/api/observe` and `/api/guide` decode and orient the uploaded image, strip metadata, and resize proportionally to `VISION_FRAME_MAX_PX` (default 640). All boxes, live dashboard frames, and saved crops use this same orientation. JPEG, PNG, and WebP are accepted; invalid images return 422 and uploads over 8 MiB return 413.
- **Vision failures:** HTTP 503 `{detail: ...}` for provider errors, timeouts, incomplete replies, or invalid labels. No sightings are saved for a failed frame. A successful empty list means no current detection, and never deletes previous sightings. The client never falls back to mock on an error.
- **Timestamps:** new `/api/observe` timestamps are normalized to UTC; invalid ISO timestamps return 422. The video importer requires a recording start time with a zone and adds each frame's presentation offset. Existing naive API timestamps are still treated as UTC.

- **`/api/where` never returns HTTP 404.** Never-seen is `200 {item, found:false, last:null, history:[], say:"I haven't seen your wallet yet."}`. An item not on the watchlist is also `found:false` with `say:"I'm not watching for X."`. Found responses include `found:true`.
- **`/api/where` `say` wording:** `"In your bedroom, on the desk, next to the lamp, about 4 minutes ago."` (room + spot as stored). `history` entries are full sighting records, newest first. `minutes_ago` is a float.
- **`item` matching:** names and aliases are case-insensitive; "my"/"the" prefixes and a trailing plural "s" are ignored ("my meds" -> medication).
- **`thumb`** is a URL path served by the backend: `/api/thumbs/<id>.jpg` (96 px max crop around the box). It is `""` if the frame could not be decoded.
- **`/api/guide` when not found:** `found:false`, `point/box/angle/clock/distance` are `null`, `pan` 0, `beep_ms` 0 (silence). `say` is `""` until the item has been missing for 2 s, then `"Lost it. Turn slowly left."` once.
- **`/api/guide` `say`** is `""` when the direction has not changed since the last cue (server tracks this per item; a pause longer than 10 s starts a fresh session). The returned `angle` is smoothed against the previous angle if it was seen within 3 s.
- **`yaw_deg`** is accepted but not used server-side; yaw compensation happens in the browser (`web/src/phone/orientation.js`). Compensation wraps the yaw difference across 0/360.
- **`clock_hour`** rounds halves up (`floor(x + 0.5)`) instead of Python's banker's `round()`, so 15 deg -> 1 and -15 deg -> 12.
- **`distance_bucket`** rounds the box height to 4 decimals before comparing, so float noise (`0.6 - 0.5 = 0.0999...`) cannot flip a boundary.
- **Extra endpoint:** `GET /api/health -> {ok, mode, mock}` so UIs can show a MOCK badge.
- **WebSocket events** all carry `mock: bool`:
  - `{type:"sighting", sightings:[sighting...], frame: <data URL | null>}` one per `/api/observe` call
  - `{type:"guide", item, frame, found, point, box, angle, clock, distance, pan, say, beep_ms}` one per `/api/guide` call
  - `{type:"say", text, source:"where"|"guide"}` whenever the app has something to speak
  - `frame` is forwarded live to the dashboard only (omitted when over 200 KB) and never stored.
- **Mock hints:** header `X-Mock-Item-X: 0.8` (or query `?mock_x=0.8`) forces the detected item's center x; `X-Mock-Item-H` / `?mock_h=0.4` forces box height (0.4 at the default y gives `"reach"`).
