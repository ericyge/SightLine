# Guidance style guide

How each guidance style sounds and when to use it. Behavior is implemented in `backend/app/guidance.py`; the setup agent picks one value for `guidance_style`.

## Rules shared by every style
- Cues are short: fewer than 8 words.
- A direction is spoken only when it changes. Silence between cues is normal, because the beeps carry the continuous information.
- Beeps pan left or right toward the item and speed up as it gets closer (800 ms apart when far, 350 near, 150 within reach). Pitch rises when the item is nearly dead ahead.
- At arm's length: "Reach out now" (once).
- If the item is out of view for 2 seconds: "Lost it. Turn slowly left."

## `clock_face`
- Speaks: "Wallet at 2 o'clock", "Wallet at 12 o'clock", "Wallet at 11 o'clock".
- 12 is straight ahead, 1 to 2 is to the right, 10 to 11 is to the left.
- Best for: people who already use clock directions (common in orientation and mobility training).
- Offer it by example: "like 'wallet at 2 o'clock'".

## `left_right`
- Speaks: "Wallet straight ahead", "Wallet slightly left", "Wallet slightly right", "Wallet turn left", "Wallet turn right".
- Small angles get "slightly", large angles get "turn".
- Best for: people who want plain words with no learning curve.
- Offer it by example: "like 'turn right'".

## `beeps_only`
- Speaks nothing at all, including "Reach out now" and "Lost it".
- Best for: people who find speech distracting, noisy rooms where speech is hard to hear, or anyone who wants to rely on the panned beeps alone.
- Offer it by example: "no speech, just beeps".

## Default
`clock_face` when the person is unsure or does not answer.

## Notes for the demo
- Wear headphones or use a loud speaker so the left/right panning is obvious to judges.
- Show `left_right` and `beeps_only` in the demo by changing the profile with `purple/validate_profile.py` and a sample from `purple/samples/`.
