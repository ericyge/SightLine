# Sightline pipeline

End-to-end data and control flow for the native app + FastAPI backend. Frames are processed then discarded; only structured sightings, places, connections, and small object crops are stored.

```
Phone (Expo)                         Backend (:8000)                      Dashboard (/ws/events)
─────────────────────────────────    ─────────────────────────────────    ─────────────────────
camera JPEG (~1/s) ──► /api/observe ─► vision.observe ─► SQLite
                       /api/calibration/frame              (places, sightings, thumbs)
voice / "Hey Jarvis" ─► /api/transcribe (optional)
                     ─► /api/chat ──────► LLM tools ─► find | where | stop | …
guidance frames ─────► /api/guide ─────► detect ─► visual beeps
                                         or localize + route ─► navigate cues
reply text ──────────► /api/speak ─────► ElevenLabs mp3 (or phone TTS)
gyroscope yaw keeps beep pan correct between /api/guide responses
```

## Modes of vision

Chosen by `PERCEPTRON_MODE` (see root `README.md` and `VISION.md`):

| Mode | Client | Used for |
|---|---|---|
| `mock` (default) | Fake detections | Full app with no API key |
| `perceptron` | `vision/vision.py` | Memory, place mapping, localization |
| `live` | `vision/live.py` | Fast guidance only (no place mapping) |

Apartment learning and room-to-room navigation need `perceptron`. `live` is detect-only and returns `204` when a prior frame is still in flight.

---

## 1. Connect and background learning

On connect, the native app (`mobile/src/Controls.tsx`) starts learning automatically while not guiding.

1. Phone captures a rear-camera JPEG (single-flight; no overlapping photos).
2. While calibration status is `mapping`, frames go to **`POST /api/calibration/frame`** (session + sequence). That path surveys places, directed connections, and objects as the user walks.
3. Otherwise frames go to **`POST /api/observe`** with optional `room`, `session_id`, and `auto_place=true`.
4. Backend runs `observe_and_save` (`memory/observation.py` + `vision.observe`):
   - Detect watchlist and open-vocabulary everyday objects.
   - For each detection, try automatic place assignment when ≥2 fixed landmarks support a match or a new `auto_<hex>` place; else leave `place_id: null`.
   - Save sighting rows + thumbnail crops; discard the full frame.
5. Broadcast a `sighting` event on `/ws/events`.
6. Loop sleeps ~1 s (or ~2 s during active mapping).

Learning pauses when guidance starts, and resumes after Stop (with a fresh memory-frame handoff so the last view is saved before navigation).

---

## 2. Voice in → intent

Hands-free **Hey Jarvis** (wake WebSocket `/ws/wake`) stays armed while guiding so the user can say “stop” without finding a button. Hold-to-talk is a backup. The mic yields while TTS plays.

1. Short audio → **`POST /api/transcribe`** (ElevenLabs STT) when configured; otherwise on-device / typed text.
2. Text → **`POST /api/chat`** with recent history and current room.
3. `llm.chat_with_fallback` returns a tool call; `_chat_response` maps it to an intent:

| Intent | When | Effect on phone |
|---|---|---|
| `where` | Item never sighted | Speak unknown answer; learning continues |
| `find` | Item has a saved sighting (“Where’s my X?” or “Find my X”) | Speak last-seen + guide cue; pause mapping; start guidance loop |
| `stop` | User stops | End guidance; resume learning |
| `set_room` / `chitchat` | Room change or other talk | Speak reply; no navigation |

Spoken reply text is app-owned (validated against profile/memory), not free-form model prose for find/where/stop. Unseen items never start navigation.

---

## 3. Guidance loop

While `guiding` is set to an item name:

1. Phone posts JPEG + optional `yaw_deg` to **`POST /api/guide`** at up to `GUIDE_MAX_FPS`.
2. Vision `detect` for that item:
   - **Visible** → `guidance.compute_guide` (pure math in `guidance.py`): box → angle → clock hour, distance bucket (`far` / `near` / `reach`), stereo `pan`, `beep_ms`, spoken cue. Mode `visual`.
   - **Not visible** → localize camera against taught/discovered places, BFS route toward the item’s newest assigned destination, check departure facing, speak the next hop. Mode `navigate` (or `lost`).
3. Phone plays spatial beeps from `pan` / `beep_ms`, compensating with gyroscope between server replies (`mobile/src/guidance.ts`). Cues go through **`POST /api/speak`** (cached ElevenLabs when possible) or device TTS.
4. Dashboard receives `guide` and `say` events.

Stop ends the loop and returns the phone to background observe/calibration frames.

---

## 4. Recall without walking

**`GET /api/where?item=…`** (also used inside chat) returns the newest sighting, optional destination place, route steps if `from_place` is known, and a spoken `say` line. Empty detections on later observes do not erase last-seen memory.

Read-only map/route helpers: `/api/memory/map`, `/api/memory/directions`, `/api/memory/items`.

---

## 5. Privacy and storage boundary

| Kept | Discarded |
|---|---|
| Sightings (item, room, spot, box, ts, thumb path, place link) | Full camera frames / video |
| Places, landmarks, directed connections | Raw STT/TTS audio (held in memory only for the request) |
| Small object crops under `THUMBS_DIR` | Chat transcripts in the database |

---

## Key files

| Stage | Code |
|---|---|
| Phone loops / handoff | `mobile/src/Controls.tsx`, `calibration.ts`, `capture.ts` |
| HTTP client | `mobile/src/api.ts` |
| Voice / wake / beeps | `mobile/src/voice.ts`, `useWake.ts`, `beeps.ts` |
| API surface | `backend/app/main.py` |
| Detect + place observe | `backend/app/vision/vision.py`, `places.py` |
| Save + assign places | `backend/app/memory/observation.py`, `store.py` |
| Calibration map walk | `backend/app/memory/calibration.py`, `mapwalk.py` |
| Visual guidance math | `backend/app/guidance.py` |
| Chat tools | `backend/app/llm.py` |
| STT / TTS | `backend/app/stt.py`, `tts.py` |

For field-level request/response shapes see [CONTRACT.md](CONTRACT.md). For Perceptron setup and video import see [VISION.md](VISION.md). For place teaching and destinations see [MEMORY.md](MEMORY.md). For the button-free apartment walk see [CALIBRATION.md](CALIBRATION.md).
