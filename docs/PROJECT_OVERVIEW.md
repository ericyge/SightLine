# Sightline: project overview

## The problem
People who are blind or have low vision lose everyday objects (keys, wallet, phone, glasses, medication) and have no quick way to find them. Existing camera assistants describe a scene in the moment. They do not remember where things were an hour ago, and they do not steer you to the object.

## What Sightline does
An audio-first companion that uses a phone camera (head or chest mounted; glasses later) for two jobs.

1. **Memory.** About once a second it looks for the user's watched items and saves each sighting: the item, the room, the spot ("on the desk, next to the lamp"), the time, and a small thumbnail. The user asks "Where's my wallet?" and hears "In your bedroom, on the desk, next to the lamp, about 4 minutes ago."
2. **Guidance.** Back in that room, the user says "find my wallet". Beeps pan left or right toward the item and speed up as it gets closer. Short spoken cues ("Wallet at 2 o'clock", "Reach out now") update continuously until the user's hand is on it.

**Why it is different:** Ray-Ban Meta and Google Astra answer about what the camera sees now. Sightline keeps persistent memory over hours and actively steers the user, and it is designed for blind users first (voice in, audio out, large touch targets).

**Privacy:** only sightings are stored (item, place, time, small thumbnail). Video is never stored, and each frame is discarded after processing.

**Out of scope:** real glasses hardware, face recognition, outdoor navigation.

## How it works

```
Phone (/phone)                         Backend (FastAPI :8000)                 Judges (/dashboard)
camera frame, 1/sec ── /api/observe ─► vision.detect ─► SQLite sightings ─┐
voice "where's X" ──── /api/where ───► latest sightings ─► spoken answer  ├─ /ws/events ─► live frame,
voice "find X" ─────── /api/guide ───► vision.detect ─► angle, clock,     │   boxes, arrow,
                                        distance, pan, beep_ms, say ──────┘   timeline, audio log
gyroscope yaw keeps the pan correct between server responses
```

- **Vision** is isolated in one class, `backend/app/vision.py`. Mock mode (the default) fakes detections so the whole app runs with no API key. The real Perceptron client is a stub for role A to build.
- **Guidance math** is a set of pure, unit-tested functions in `backend/app/guidance.py`: pixel position becomes an angle, then a clock hour, a distance bucket, a stereo pan and a beep interval.
- **The frontend** is React + Vite with plain CSS. It uses browser APIs only: camera, Web Audio, speech synthesis and recognition, device orientation. It never calls Perceptron, because the backend owns the API key.

## Stack
Python 3.11+, FastAPI, uvicorn, SQLite, pydantic, pytest. React + Vite. Perceptron vision API (`perceptron-mk1.5`, `isaac-0.3-fast`). Purple (UW's no-code agent builder) for the setup agent.

## Team and ownership
| Role | Area |
|---|---|
| A | Vision and memory: Perceptron client, prompts, `/observe`, `/where` |
| B | Guidance and audio: guidance math, `/guide`, beeps, speech, gyroscope |
| C | App and dashboard: phone UI, judge dashboard, ngrok, integration, error states |
| D | Purple, product and pitch: setup agent, Devpost, demo script, outreach to a blind user |

Checklists and checkpoints are in [ROLES.md](ROLES.md).

## Hackathon tracks
Community Kitchen (main), Perceptron (our vision API), nebulaONE (the Purple "Sightline Setup" agent turns 3 questions into a profile JSON that is POSTed to `/api/profile`), SideShift, DubHacks Next.

## Current status
The scaffold runs end to end in mock mode: backend endpoints, WebSocket events, guidance math (48 passing tests), phone page, and dashboard. Not yet done: the real Perceptron client, on-device verification of speech, audio and gyroscope behavior, and the Purple agent. See [ROLES.md](ROLES.md).

## Where to read next
- [README.md](../README.md): run it in two minutes
- [CONTRACT.md](CONTRACT.md): API contract and how the scaffold resolves open details
- [ROLES.md](ROLES.md): who owns what, TODOs, checkpoints
- [CLAUDE.md](../CLAUDE.md): conventions for Claude Code sessions
