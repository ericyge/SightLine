# API contract (backend implements, frontend consumes; keep field names EXACTLY)

Stored records:
  item:     { name, aliases[], description }
  room:     { name }
  sighting: { item, room, spot, box:[x1,y1,x2,y2] (0..1), confidence, ts (ISO), thumb (path) }

Endpoints:
  POST /api/observe   multipart: frame (JPEG), room (string), ts (optional ISO)
      -> { sightings: [sighting...] }   saves sightings found in this frame, pushes an event to /ws/events
  GET  /api/where?item=wallet
      -> { item, last: { room, spot, minutes_ago, thumb, confidence }, history: [ ... up to 5 ... ], say: "On your bedroom desk, next to the lamp, about 4 minutes ago." }  (404-style {found:false, say:"I haven't seen your wallet yet."} if never seen)
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

Everything above is verbatim from the project brief. The items below are what the scaffold actually does; change them deliberately and update this file.

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

---

# Conversational layer (added after the original contract)

  POST /api/chat   JSON: { text, history:[{role:"user"|"assistant", content}] (last 6 used), current_room? }
      -> { intent:"find"|"where"|"stop"|"set_room"|"chitchat", item?, room?, say, provider }
  WS event: {type:"chat", user_text, intent, item, say, provider, mock}

- `text` is 1 to 500 characters. `provider` is whichever answered: `mock` (keyword matching, the default), `purple`, `gemini`, `openai_compat`, or `keyword_fallback` (every configured provider failed, timed out or had no key).
- **Provider order: purple -> gemini -> keyword.** The chain starts at `LLM_PROVIDER` and falls through the later providers on any error, timeout (3 s) or missing key; it always ends at keyword matching. `LLM_PROVIDER=purple` tries purple, gemini, keyword. `gemini` tries gemini, keyword. `mock` and `openai_compat` do not use gemini. A provider with no key configured is skipped without a request.
- **Privacy, all external providers:** only the user's transcribed text, the last 6 turns, and watchlist names, aliases and room names (plus the current room) are sent. Never camera frames, thumbnails, descriptions, home spots or sighting history. Keys, Authorization headers and request bodies are never logged.
- The backend asks a model (tool calling: `find_item`, `where_is_item`, `stop_guidance`, `set_room`) what the user wants. **`item` and `room` are validated against the stored profile** (names, aliases, rooms). An unknown item or room becomes `intent:"chitchat"` with one short question in `say`.
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
