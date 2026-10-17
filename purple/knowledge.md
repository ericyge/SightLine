# Sightline: knowledge for the Setup agent

Upload this file as the agent's knowledge file.

## What Sightline is
Sightline is an audio-first assistant for blind and low-vision people. A phone camera, worn on the head or chest, watches for a short list of everyday items. It remembers where it last saw each one, with the room, the spot, and the time. The user can ask "Where's my wallet?" and hear the answer. Back in that room, the app guides them to the item with beeps that pan left and right and speed up as they get closer, plus short spoken directions.

Privacy: the app stores only item, place, time, and a small thumbnail. It never stores video. Each camera frame is thrown away after it is processed.

Sightline does not recognize faces, does not do outdoor navigation, and does not need special glasses; it works with a phone.

## The profile format
The agent produces JSON with exactly these keys:

| Key | Type | Rule |
|---|---|---|
| `watchlist` | list of items | 1 to 8 items |
| `watchlist[].name` | text | one or two lowercase words, singular |
| `watchlist[].description` | text | what it looks like: color, material, size, shape |
| `watchlist[].home_spot` | text | room plus surface, lowercase |
| `rooms` | list of text | lowercase room names, 1 to 6 |
| `guidance_style` | text | exactly `clock_face`, `left_right`, or `beeps_only` |

## Guidance styles
- `clock_face`: speaks the direction as a clock hour. Straight ahead is 12 o'clock, a little right is 1 or 2, left is 10 or 11. Example: "Wallet at 2 o'clock." Good for people who already think in clock directions.
- `left_right`: speaks plain directions: "straight ahead", "slightly left", "turn right". Good for people who prefer simple words.
- `beeps_only`: no speech at all, just beeps that pan toward the item and speed up as it gets closer. Good for people who find speech distracting.

All styles say "Reach out now" with speech when the item is within reach (except `beeps_only`, which stays silent).

## Standard item names
Use these names when they fit. The app already understands common alternate words for them.

| Name | Also heard as | Plain default description | Typical home_spot |
|---|---|---|---|
| keys | key | keys on a keychain | kitchen counter |
| wallet | | a wallet | bedroom desk |
| phone | cell phone, mobile | a smartphone | living room table |
| glasses | eyeglasses, specs | a pair of glasses | bedroom nightstand |
| medication | meds, pills, medicine | a pill bottle | kitchen counter |

Other items are fine too (for example "water bottle", "cane", "headphones", "remote"). Smaller or flatter items are harder for a camera to see, so descriptions with a distinct color or a bright keychain, case, or sticker help a lot.

## Writing good descriptions
Describe what a camera would see, in a few words:
- Good: "red leather wallet", "keys on a bright orange keychain", "white pill bottle with a blue cap", "black rectangular phone in a green case".
- Weak: "my wallet", "the usual keys", "important stuff".
If the person gives a weak description, ask once: "What color is it, and what is it made of?"

## Writing good home spots
Room plus surface or furniture, lowercase: "bedroom desk", "kitchen counter", "living room table", "bedroom nightstand", "hallway shelf". Avoid directions like "to the left of the door"; the camera works out the exact spot itself.

## Example conversations

Example A
- Agent: What do you often lose? Tell me each item and what it looks like.
- Person: My wallet, it's red leather, and my keys with a bright orange keychain.
- Agent: Where does each item usually live, and which rooms should I watch?
- Person: Wallet on the bedroom desk, keys on the kitchen counter. Bedroom and kitchen.
- Agent: How should I guide you? Option one: clock face. Option two: left and right. Option three: beeps only.
- Person: One.
- Agent: Got it. I'll watch for your wallet and keys in the bedroom and kitchen, and guide you with clock face directions.
```json
{
  "watchlist": [
    {"name": "wallet", "description": "red leather wallet", "home_spot": "bedroom desk"},
    {"name": "keys", "description": "keys on a bright orange keychain", "home_spot": "kitchen counter"}
  ],
  "rooms": ["bedroom", "kitchen"],
  "guidance_style": "clock_face"
}
```

Example B (person is unsure)
- Person (to question 3): I don't know, whichever is easiest.
- Agent: I'll start with clock face. You can change it later.  Then the JSON with `"guidance_style": "clock_face"`.

Example C (person prefers no speech)
- Person (to question 3): Just the beeps, please.
- Result: `"guidance_style": "beeps_only"`.
