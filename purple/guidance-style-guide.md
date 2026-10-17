# Guidance style guide

Placeholder. TODO(D): describe how each style should sound and when to use it. Current behavior (see `backend/app/guidance.py`):

- `clock_face`: "Wallet at 2 o'clock", then "Reach out now"
- `left_right`: "Wallet slightly left" / "turn right" / "straight ahead", then "Reach out now"
- `beeps_only`: no speech, beeps only

Cues stay under 8 words and are spoken only when the direction changes. If the item is lost for 2 seconds: "Lost it. Turn slowly left."
