# Apartment demo checkpoints

Target: one apartment, at most five rooms, a wallet and optionally a few distinct everyday
objects. The camera sees the wallet, keeps running while the user walks away, then responds
to a request with spoken directions back and visual guidance at the destination.

Implement one checkpoint at a time and let the user verify it before starting the next.
The agreed first-use flow is automatic calibration: walk the apartment to build its map, then
ask about objects. Users never supply a starting place; live guidance recognizes it from the camera.

## Step 1: directions only from supported locations — implemented

Routing uses the newest assigned place among recent sightings. A newer unassigned sighting
does not erase an older mapped destination; surface descriptions and room names still never
invent a place link. Directions queries require an explicit known starting place; the live
guide uses camera localization. Routes use saved connections in their recorded direction only.

Run from the existing backend directory:

```bash
cd /Users/eric/Files/DubHack26/Sightline/backend
../venv/bin/python -m pytest tests/test_navigation_checkpoint.py -q -s
```

Expected: **13 passed**, with printed spoken responses for these scenarios:

- Known bedroom doorway and wallet at kitchen counter: hallway instruction, counter
  instruction, arrival message, then visual guidance when mock detection sees the wallet.
- Missing/invalid starting place: `unknown_current`, no walking steps.
- A newer unassigned wallet sighting: falls back to the older assigned destination (spot text
  and room names still do not invent a place). Only when no recent sighting is assigned:
  `unknown_destination`.
- A manually cleared assignment: no route until the assignment is confirmed again.
- Camera cannot identify the user's place: `lost`, no beeps/walking steps, a request to scan
  slowly. When the camera recognizes the place again, its instruction is spoken again.
- Only a forward connection exists: `no_path` for reverse travel. Saving a separate return
  connection enables its exact recorded instruction.
- Wallet never seen: no destination or route.
- CLI and HTTP queries follow the same newest-sighting and starting-location rules.

Each test uses a temporary database and generated image. No provider calls, real memory changes,
server startup, or API keys are needed. These checks simulate vision and verify application
behavior only. They do not establish camera accuracy, correct facing, obstacle avoidance, or
working phone audio. Restart an already running backend to load the changed behavior.

## Step 2: native live calibration — implemented, awaiting user trial

Automatically learn from the camera while connected and not guiding, discovering places and directed paths,
remember open-vocabulary objects, and report missing return paths. Native observation now uses
automatic room recognition and session context. See [CALIBRATION.md](CALIBRATION.md) for the
mock checkpoint and physical two-room walkthrough. The full camera-to-navigation demo is not
verified merely by passing software tests.

## Step 3: departure-facing gate — implemented, awaiting physical trial

Live guidance compares the current view with the next recorded departure before issuing its
walking instruction. Uncertain/sideways views request a scan or turn in place; visible blockage
holds the cue. Each recognized next place is checked again. Arrival and visible-object handoff
remain, and native camera errors now announce a pause/retry. See [NAVIGATION_TRIAL.md](NAVIGATION_TRIAL.md).
These checks do not establish body heading or continuous obstacle avoidance.

## Next checkpoints

1. Verify calibration names, landmark matching, both route directions, and object destinations
   in the actual two-room space. Fix accuracy and latency issues revealed by that trial.
2. Validate the implemented facing gate, progress/arrival, uncertainty recovery, and spoken
   timing on the phone. Fix failures revealed by real frames before adding further navigation.
3. Rehearse see wallet → walk away → ask → return → visually reacquire. Expand to five rooms
   and additional objects after the two-room trial works. Consolidate repetitive sightings.

## Interaction refinement: automatic learning

Learning begins on connection and resumes after guidance, without explicit calibration actions.
Recall leaves learning active; Find of an unseen object answers unknown without starting guidance.
See CALIBRATION.md. This removes a setup interaction, not the need to walk and observe both paths.
