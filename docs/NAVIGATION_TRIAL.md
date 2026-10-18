# Facing and return-to-object checkpoint

This follows native apartment calibration. No starting location is supplied: the current camera
view identifies the place and checks facing against the next saved departure view. A room match
alone does not release a walking instruction. The full `perceptron` client performs this in its
existing localization request, so a hidden-object guidance frame still uses at most two vision
requests (object detection, then localization/facing). No new keys or database migration.

## Offline test

From the backend directory:

```bash
cd /Users/eric/Files/DubHack26/Sightline/backend
../venv/bin/python -m pytest tests/test_departure.py -q -s
```

Expect 10 passing scenarios (including parameterized cases). The output prints turn, hold,
walk, and blockage responses. Tests use generated images, mocked vision, and temporary memory;
they do not use the real apartment, keys, or services. Additional offline SDK tests validate
structured facing replies, required fields, and insufficient landmark evidence.

## Phone trial

1. Restart the backend in `PERCEPTRON_MODE=perceptron` and reload the native app. Keep the same
   real database used for calibration. If you have not mapped the apartment yet, follow
   [CALIBRATION.md](CALIBRATION.md), including the return walk. There is no finish button requirement.
2. Leave the wallet on its recorded counter. Walk to the other mapped room with the feed running.
3. Ask “Where is my wallet?” to check memory, then “Find my wallet” to start live guidance.
4. Begin with the camera pointing away from the recorded departure. Expect a pause/scan cue, or
   a turn-in-place cue if the model can see which side the departure is on. An unknown camera
   place produces the existing localization scan request.
5. Turn slowly in place toward the departure view. Only after alignment should the next saved
   walking instruction be spoken. Keep the camera facing the direction of travel for this demo.
6. At the next recognized place, expect its own facing check and next instruction. At the saved
   destination, expect “Look around slowly.” Once the wallet is visible, expect visual beeps
   and object-relative cues. Arrival at a place does not establish that the wallet is still there.
7. While stationary, interrupt the backend connection. Expect one spoken pause/retry warning
   and no retained visual beeps. Restore it: fresh results should resume cues. Repeated failures
   should not repeat the warning continuously.

Say “Stop” to resume background learning after guidance. A recall request also ends current
guidance and returns to learning.

First assess these cues with a sighted teammate observing the scene. Record incorrect place
matches, wrong turn directions, premature alignment, and response delay before a walking trial.
Passing offline tests does not establish physical navigation accuracy. No obstacle avoidance,
metric position, camera-to-body heading estimate, or continuous collision monitoring is provided.
A `blocked` reply means the model noticed an obstruction; `aligned` does not guarantee clearance.
Fast guidance uses the existing built-in/cached voice path; ElevenLabs chat replies are unchanged.

## Mock controls and inspection

For endpoint testing with a separate mock database, use `X-Mock-Hide: 1` and an existing
`X-Mock-Place` ID. Add `X-Mock-Facing: aligned|left|right|blocked|unknown` (or `mock_facing` query).
Without a facing hint, mock guidance holds instead of assuming alignment. Real Perceptron ignores
all mock hints. `/api/guide` now returns `departure_status`; `next_instruction` is null until
aligned, and null at arrival or in visual/lost mode. The stored directions inspection endpoint
still lists the route without a camera-facing check; it is not live movement authorization.
