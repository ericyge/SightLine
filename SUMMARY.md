# Sightline change summary

## Latest update: automatic apartment learning and honest unknown-item answers

You no longer need to start or finish calibration. The native app learns places, paths, and
objects while connected and not actively guiding. You can ask questions anytime. If an object
has never been seen, both Where and Find answer: “I don't know where your wallet is yet.
I haven't seen it.” Find does not start navigation for an unseen object.

## What changed in this update

- **`mobile/src/Controls.tsx`:** connecting starts/resumes learning automatically, including for
  existing maps. Normal place-recognition announcements are quiet; errors/recovery remain spoken.
  Manual calibration controls moved into the initially hidden debug panel. The normal screen
  describes background learning instead of requiring Start/Finish actions.
- **Command handoff:** requests briefly pause new capture and wait for the current memory frame.
  Where/unknown-item replies leave learning active. A Find response for a remembered item
  automatically saves/pauses mapping before guidance begins. Stop resumes learning with fresh
  camera continuity. Recall also ends any previous guidance, preventing directions to a previous
  target from continuing alongside an unknown-location answer. Explicit debug/voice calibration
  controls remain overrides; reconnecting starts learning again.
- **`mobile/src/calibration.ts`:** added a testable asynchronous handoff helper. Saving must
  succeed before navigation starts; a failed memory frame does not prevent querying prior memory.
- **`backend/app/main.py`:** Find tool calls now check for a saved sighting before returning a
  navigation intent. Unseen objects receive a normal Where response with an explicit unknown
  answer. The rule applies to model calls and keyword fallback, aliases, and open-vocabulary
  objects. Known sightings retain existing navigation/location/facing checks.
- **Tests:** updated `backend/tests/test_chat_api.py` and `test_api.py` for the new message and
  no-navigation behavior; positive Find tests explicitly create a sighting. Added tests for
  unseen watchlist/alias/open-vocabulary requests and model instructions that conflict with
  memory. Added three mobile handoff tests in `mobile/src/pure.test.mjs` for frame ordering,
  recall versus navigation, and failure handling.
- **Docs:** updated root README, the calibration and navigation walkthroughs, API contract,
  memory guide, checkpoint list, documentation index, and mobile README. Existing AGENTS.md
  already requires loading these docs; no rule change was necessary.

## How to test

1. Restart the backend in `perceptron` mode and reload the native app, using your real demo
   database. Connecting should say it is learning automatically. Do not press calibration buttons.
2. Walk through two rooms and back. Places/connections and sightings should accumulate quietly.
3. Ask for an object absent from this database. Both “Where is my stapler?” and “Find my stapler”
   should answer unknown, with no new guidance. Learning continues.
4. Show that object clearly on a surface, wait for a processed frame, then ask Where again.
   It should report its saved sighting. Find may now start guidance, subject to mapped destination,
   current-place, route, and facing evidence.
5. Say Stop during guidance. The app should resume background learning automatically.

An object that is currently out of view may still have a historical sighting; that correctly
produces a last-seen answer rather than “never seen.” Use a separate test database or a genuinely
unseen object to verify step 3. Detailed setup is in `docs/CALIBRATION.md`.

## Verification

- Full backend suite: **394 passed**, with one existing Starlette/httpx warning.
- Mobile tests: **33 passed**, including the three new handoff tests.
- Mobile lint and TypeScript checks: passed.
- iOS and Android production JavaScript bundles exported successfully to a temporary directory;
  this verifies bundling, not behavior on physical phones.
- Real Perceptron recognition, phone lifecycle/audio, and physical navigation were not tested.
- No keys, existing memory, dependency versions, or virtual environments were changed.

## Earlier work and limits

Prior uncommitted calibration, open-vocabulary fixes, departure-facing gate, and camera-error
speech remain. These are not new changes in this update. The underlying calibration endpoints
still save each processed frame; there is no new database migration. Learning runs with the
foreground app/camera active, not when the phone app is closed or locked. Full `perceptron`
mode is required for real mapping; mock data is scripted, and fast `live` mode cannot map.
Continuous learning still needs views in both travel directions and may find incomplete routes.
Physical camera/facing accuracy, obstacle avoidance, deduplication, and the apartment rehearsal
remain unfinished. Low-level `/api/guide` stays available for explicit camera tests; the chat
entry point is what prevents unseen-object navigation.
