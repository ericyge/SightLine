# Live apartment calibration

First use starts with an apartment walkthrough. The user does **not** name a starting place,
assign rooms, or write map JSON. The camera discovers places from fixed landmarks. Later,
`Find my wallet` uses the current camera view to recognize where the user is and routes toward
the wallet's newest assigned sighting. An explicit `from_place` is only needed for inspection
queries with no camera attached.

This checkpoint provides background learning in the **native Expo app** (`mobile/`). The browser
prototype has no calibration controls yet. Start with two rooms, then expand to the intended
apartment of up to five rooms after inspecting the resulting map and testing recognition.

## Test without a phone or API calls

```bash
cd /Users/eric/Files/DubHack26/Sightline/backend
../venv/bin/python -m pytest tests/test_calibration.py -q -s
```

The first test prints a simulated doorway → hallway → kitchen counter → hallway → doorway
walk. It begins with an empty map and no supplied location. The wallet, cup, and chapstick appear at the mock
counter. The latter two demonstrate off-watchlist memory in the scripted fixture. The outward leg has missing return paths; after walking back, all three places have
routes in both directions. Other cases verify restarts, incomplete walks, unknown views, failed
inference, duplicate frames, stale capture timestamps, and independent object destinations.
All data is temporary. No real memory changes or provider calls occur.

## Test on your phone with Perceptron

Use a new database for this first trial so earlier mock places and experimental maps do not
confuse recognition. Stop your existing backend first, then run this in a terminal:

```bash
cd /Users/eric/Files/DubHack26/Sightline/backend
DB_PATH=sightline-calibration-demo.db THUMBS_DIR=thumbs/calibration-demo PERCEPTRON_MODE=perceptron \
  ../venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The command reads the existing root `.env` for the Perceptron key and overrides only these three
settings for this process. **Use the same database path on later runs to keep this map.** It does
not change `.env`. The full `perceptron` mode is required: the faster `live` detect-only client
cannot survey or localize places. Calibration refuses that mode with an explanatory error.

Start/reload the native app as usual (`npm start` in `mobile/`) and connect to the backend's
LAN address or existing HTTPS tunnel. The laptop's `localhost` address does not work on a phone.

1. Connect the native app to the backend. Learning starts automatically, including when an
   apartment map already exists. There is no required start/finish button.
2. Slowly scan the wallet's counter, fixed nearby landmarks, doorways, and a second room.
   Keep the camera running as you move. Places, directed connections, and objects save as each
   frame is processed. Normal place-recognition announcements are quiet; failures still speak.
3. Walk both directions. A return route is learned from that view rather than invented from
   the outward route. The screen reports how many places/connections have been saved.
4. Ask “Where is my wallet?” at any time. The current frame finishes before recall. Learning
   continues after the answer. If there is no sighting, the answer is “I don't know where your
   wallet is yet. I haven't seen it.” Asking “Find” for an unseen item gives the same answer.
5. “Find my wallet” with a saved sighting automatically saves/pauses learning and starts guidance.
   Missing route/location evidence still withholds walking instructions. Say “Stop” to end
   guidance and resume background learning with fresh camera continuity.
6. Follow [NAVIGATION_TRIAL.md](NAVIGATION_TRIAL.md) to verify facing and route accuracy.
   Manual Start/Finish/Continue controls remain under Show debug counters for troubleshooting;
   explicit calibration voice commands also remain available as overrides.

For a no-key **phone UI** check, use the same command with `PERCEPTRON_MODE=mock` and distinct
paths such as `DB_PATH=sightline-calibration-mock.db THUMBS_DIR=thumbs/calibration-mock`. Connect the app and wait about 20 seconds: ten simulated frames complete the out-and-back map. This
ignores the actual apartment images. Never reuse that mock database for a real walkthrough.

## What to verify at this checkpoint

- The map starts without asking for room names or a starting place.
- Recognized names and landmarks describe the real apartment; repeated views match existing
  places rather than making duplicates. Room labels are model descriptions, not measured room IDs.
- Both outward and return connections exist. A generic room count does not establish coverage.
- The wallet's sighting has its own supported destination. Camera position does not automatically
  become every visible object's destination.
- Every processed frame saves map progress; reloading preserves the map. Reconnecting a mapping session recognizes the camera again
  instead of treating an old camera position as current.

Inspect data in a browser at your backend's `/api/calibration`, `/api/memory/map`, and
`/api/where?item=wallet`. `/docs` exposes the calibration endpoints for manual inspection too.

## Behavior and limits

- The phone sends one calibration frame at a time, at most one every two seconds. Each accepted
  frame makes a place-survey request followed by a separate object-memory request. Default full
  client timeout is 15 seconds per call; the phone allows 40 seconds for calibration/guidance
  and 20 seconds for memory uploads. Real device latency has not been measured for this flow.
- New/matched places require at least two distinct fixed landmarks. Connections additionally
  require a model-reported direct transition, a walking instruction, departure/arrival views,
  and a recent previous camera frame. A missing instruction never becomes a generic walking cue.
- Unknown views, sequence gaps, restarts/resumes, provider failures, or more than ten seconds
  between captures break transition continuity. They do not erase saved places. Retrace unclear
  transitions to record the missing connection.
- `coverage_ready` means at least two stored places have directed paths between every pair.
  It does **not** establish that every apartment room was visited, the model's paths are correct,
  the user's facing direction is correct, or that obstacles have been checked. Finish saves an
  incomplete map as well. No blind walking trial has been performed by the agent.
- One apartment, one active phone walk, **one backend worker** are supported. Use the default
  uvicorn worker count. This checkpoint is not a multi-camera/multi-worker session service.
- The existing place candidate limit is 40 places (places can be doorways/surfaces, not just
  rooms). New survey places are refused at that limit rather than silently forgetting earlier ones.
- The server stores map text, calibration metadata, sightings, and object crops. One previous
  image is held only in RAM for comparing nearby views, replaced on the next accepted frame and
  cleared on finish/resume/error. It is never written into the database or saved as a full image.
- A failed object-memory call leaves the successfully surveyed map intact and reports
  `memory_warning`; it does not fabricate a sighting. The phone announces failures.
- Existing saved connections from earlier experiments are retained, not revalidated or deleted.
  A fresh database is recommended for this trial.

Expo SDK 57 camera/UI integration was checked against the
[versioned camera documentation](https://docs.expo.dev/versions/v57.0.0/sdk/camera/) and
[React Native accessibility documentation](https://reactnative.dev/docs/accessibility).
