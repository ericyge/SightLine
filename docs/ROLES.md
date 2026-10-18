# Roles

Four owners, one file area each, so merges stay quiet. Search the code for `TODO(A)` .. `TODO(D)` to find where to start. The repo runs in mock mode with no keys; build on that.

## A: Vision and Memory
**Owns:** `backend/app/vision/`, `backend/app/memory/`, `backend/app/frames.py`, `backend/app/db.py`, `/api/observe`, `/api/where`, `/api/memory/*`.
Real Perceptron client, prompts + JSON schemas, in-context photos of each item, room tagging, automatic and manual place assignment.

**NEXT TASK:** verify automatic place discovery/matching, `vision.localize`, and navigate cues on
real clips with [MEMORY.md](MEMORY.md). Memory, automatic place assignment, camera localization,
and directed BFS routing are implemented in mock; live Perceptron accuracy for localization is
unverified. The first real Mk1.5 clip run confirmed authentication and structured-output
acceptance (eight frames, five sightings, no errors). Next list candidate account models and
measure median/p95 latency on 20 real 640px phone frames. Start with `perceptron-mk1.5`;
compare `isaac-0.3-fast` only if available and compatible. Prepare the results for the teammate
to share in team chat; agents must not send messages without explicit user authorization.
Targets: memory about 1/sec, guidance under 1.2 s round trip. The CLI benchmark excludes phone
upload and audio playback, so measure the full guidance loop separately on the phone.

- [ ] Live model acceptance and latency: 20 real 640px frames per supported candidate; record medians/p95 and failures
- [x] Implement `PerceptronVision.detect` and `observe`; installed SDK tested with offline HTTP responses
- [x] Verify `image(bytes)` and message API; use explicit JSON boxes and labeled reference images
- [x] Prompts + JSON schema for item + spot ("on the desk, next to the lamp") and per-object `PlaceEvidence`
- [x] Support local reference-photo manifests (actual item photos and accuracy testing still needed)
- [x] User-supplied and optional automatic room tags through the phone flow and video importer
- [x] Persistent taught and automatically discovered places, text landmarks, directed connections, and per-sighting destination links (`place_source`, `place_reason`)
- [x] Automatic per-object place discovery and landmark matching during `/api/observe` and video ingestion
- [x] Memory API/CLI, automatic legacy migration, and newest-sighting destination recall
- [x] Match current camera views to taught places (`vision.localize`; facing not inferred)
- [x] Route selection via directed BFS (`memory.plan_route`) wired into `/api/guide` navigate mode
- [ ] Live arrival/obstacle checks with B/C; live Perceptron verification of localization
- [ ] Automatic room recognition (live Perceptron verification)
- [x] Recorded-video importer and latency benchmark tooling; tested offline
- [ ] Dedupe/throttle sightings so the timeline is not spammed
- [ ] Layer 0: recorded video becomes memory, "where" answers aloud

Recorded-video import and a latency benchmark are ready in `python -m app.vision.cli`.
See `docs/VISION.md`. Mk1.5 accepted a first real clip; broader accuracy, latency benchmarks,
and other model compatibility remain unverified; the Layer 0 checklist above refers to the real demo.

Integration notes for B/C: real `confidence` is nullable; provider failures are HTTP 503,
not `found:false`; boxes refer to the prepared upright frame. The importer writes memory but
does not publish WebSocket events. Keep mock trials in a separate database from real trials.
Sightings now include `id` and `place_id`; `/where` adds a nullable `destination` place.
Navigation falls back to the newest assigned place among recent sightings when the latest
observation is unmapped. Connections are executed as navigate cues when localization
succeeds; facing and obstacles are unverified.

## B: Guidance and Audio
**Owns:** `backend/app/guidance.py`, `/api/guide`, `web/src/phone/audio.js`, `speech.js`, `orientation.js`.
Beep engine, spatial pan, gyroscope smoothing. Stretch: hand guidance with MediaPipe Hands ("left a little... down... got it").

- [ ] Guidance math verified on still photos (Layer 0)
- [ ] Tune beep pitch/volume/interval so panning is obvious in headphones
- [ ] Verify yaw sign and drift on real iPhone and Android (`orientation.js` is unverified on devices)
- [ ] Tune speech cadence; confirm "speak only on change" feels right
- [ ] Voice command phrasing in `parseCommand`
- [ ] ElevenLabs speech for confirmed navigation cues (browser SpeechSynthesis speaks `say` today)
- [ ] Stretch: MediaPipe Hands final-reach guidance

## C: App and Dashboard
**Owns:** `web/src/phone/Phone.jsx`, `web/src/dashboard/`, deployment/ngrok, integration, error states, offline fallback.

- [ ] Phone works over the ngrok HTTPS URL on a real phone
- [ ] Camera-denied / no-network / server-down error states
- [ ] Offline fallback (queue sightings, say so)
- [ ] Dashboard timeline backfill on load (needs a small list endpoint) and repeat collapsing
- [ ] Dashboard readable from 3 m away (judge-facing)
- [ ] Integration: full flow memory -> where -> guide -> reach (Layer 2)
- [ ] Place teaching/assignment UI and current-location handoff; phone currently sends only a room

## D: Purple, Product and Pitch
**Owns:** `purple/` folder, the Purple "Sightline Setup" agent (3 questions in, profile JSON out; POST the result to `/api/profile`), LinkedIn post, Devpost, demo script, props, backup video, outreach to a blind user.

- [ ] Purple "Sightline Setup" agent: 3 questions in, profile JSON out (match `purple/sample-profile.json`)
- [ ] Agent instructions + knowledge file saved in `purple/`; fill `purple/guidance-style-guide.md`
- [ ] Demo script and props
- [ ] Backup demo video
- [ ] Outreach to a blind or low-vision user for feedback
- [ ] LinkedIn post
- [ ] Devpost draft (all 4 members, tracks declared)

## Checkpoints
| When | What |
|---|---|
| Layer 0, 4 PM Sat | Recorded video becomes memory, "where" answers aloud, guidance math on still photos |
| Layer 1, 8 PM Sat | Live camera memory + live beeps |
| Layer 2, 10 PM Sat | Full flow + dashboard |
| Before 10:30 PM Sat | Devpost draft (all 4 members, tracks declared) |
| Before 10:00 AM Sun | Final submission |

Tracks: Community Kitchen (main), Perceptron, nebulaONE (a Purple agent), SideShift, DubHacks Next.
