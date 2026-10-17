# Roles

Four owners, one file area each, so merges stay quiet. Search the code for `TODO(A)` .. `TODO(D)` to find where to start. The repo runs in mock mode with no keys; build on that.

## A: Vision and Memory
**Owns:** `backend/app/vision.py`, `backend/app/db.py`, `/api/observe`, `/api/where`.
Real Perceptron client, prompts + JSON schemas, in-context photos of each item, room tagging.

**FIRST TASK:** measure Perceptron median latency on 20 real 640px phone frames for `perceptron-mk1.5` and `isaac-0.3-fast`, and post the numbers in the team chat. Targets: memory about 1/sec, guidance under 1.2 s round trip.

- [ ] Latency benchmark: 20 real 640px frames x 2 models, post medians
- [ ] Implement `PerceptronVision.detect` (see the verified/unverified notes in its docstring)
- [ ] Verify unknowns: bytes vs path input for `image()`, point attribute names, `examples` shape
- [ ] Prompts + JSON schema for item + spot ("on the desk, next to the lamp")
- [ ] In-context example photos for each watched item
- [ ] Room tagging (user dropdown now; auto-detect stretch)
- [ ] Dedupe/throttle sightings so the timeline is not spammed
- [ ] Layer 0: recorded video becomes memory, "where" answers aloud

## B: Guidance and Audio
**Owns:** `backend/app/guidance.py`, `/api/guide`, `web/src/phone/audio.js`, `speech.js`, `orientation.js`.
Beep engine, spatial pan, gyroscope smoothing. Stretch: hand guidance with MediaPipe Hands ("left a little... down... got it").

- [ ] Guidance math verified on still photos (Layer 0)
- [ ] Tune beep pitch/volume/interval so panning is obvious in headphones
- [ ] Verify yaw sign and drift on real iPhone and Android (`orientation.js` is unverified on devices)
- [ ] Tune speech cadence; confirm "speak only on change" feels right
- [ ] Voice command phrasing in `parseCommand`
- [ ] Stretch: MediaPipe Hands final-reach guidance

## C: App and Dashboard
**Owns:** `web/src/phone/Phone.jsx`, `web/src/dashboard/`, deployment/ngrok, integration, error states, offline fallback.

- [ ] Phone works over the ngrok HTTPS URL on a real phone
- [ ] Camera-denied / no-network / server-down error states
- [ ] Offline fallback (queue sightings, say so)
- [ ] Dashboard timeline backfill on load (needs a small list endpoint) and repeat collapsing
- [ ] Dashboard readable from 3 m away (judge-facing)
- [ ] Integration: full flow memory -> where -> guide -> reach (Layer 2)

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
