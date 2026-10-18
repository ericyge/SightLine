"""Guidance math. Pure functions only: no I/O, no clock reads (time is passed in).

Owner: B. See docs/CONTRACT.md for the formulas this implements.
"""
import math
from dataclasses import dataclass

LOST_AFTER_S = 2.0          # not found this long -> "Lost it"
SMOOTH_WINDOW_S = 3.0       # only smooth against an angle seen this recently
CENTER_DEG = 8.0            # |angle| <= this counts as "on target"


def angle_deg(x: float, fov_h: float = 65.0) -> float:
    """Horizontal angle of x (0..1, left to right) from camera center, in degrees."""
    return (x - 0.5) * fov_h


def clock_hour(angle: float) -> int:
    """Clock-face hour for an angle: 0 deg = 12, +30 = 1, -30 = 11.

    Rounds halves up (floor(x + 0.5)) so the result is deterministic, unlike Python's round().
    """
    hour = math.floor(angle / 30 + 0.5) % 12
    return 12 if hour == 0 else hour


def distance_bucket(box: list[float], center_y: float) -> str:
    """Rough distance from box height: far / near / reach."""
    h = round(box[3] - box[1], 4)  # rounding keeps boundaries exact (0.6 - 0.5 < 0.1 in floats)
    if h < 0.10:
        return "far"
    if h <= 0.30:
        return "near"
    return "reach" if center_y > 0.5 else "near"


def pan(angle: float) -> float:
    """Stereo pan -1..1; full pan at +-30 degrees."""
    return max(-1.0, min(1.0, angle / 30))


def beep_ms(distance: str) -> int:
    """Beep interval for a distance bucket."""
    return {"far": 800, "near": 350, "reach": 150}[distance]


def pitch_boost(angle: float) -> bool:
    """True when the item is nearly dead ahead (higher pitch beep)."""
    return abs(angle) <= CENTER_DEG


def smooth(prev_angle: float, new_angle: float) -> float:
    """Moving average, newest weight 0.5."""
    return 0.5 * new_angle + 0.5 * prev_angle


def _wrap180(deg: float) -> float:
    """Wrap an angle difference into [-180, 180)."""
    return (deg + 180) % 360 - 180


def yaw_compensate(last_angle: float, current_yaw: float, yaw_at_last: float) -> float:
    """Angle to the item after the phone turned since the last vision update.

    corrected = last_angle - (current_yaw - yaw_at_last), with the yaw difference wrapped
    so crossing 0/360 does not jump. Yaw is clockwise-positive, like the angle.
    """
    return last_angle - _wrap180(current_yaw - yaw_at_last)


def _direction(angle: float) -> str:
    """left_right bucket for an angle."""
    if abs(angle) <= CENTER_DEG:
        return "ahead"
    side = "right" if angle > 0 else "left"
    return ("slightly " if abs(angle) <= 20 else "turn ") + side


def say(item: str, angle: float | None, clock: int | None, distance: str | None,
        style: str, *, found: bool = True, lost_s: float = 0.0,
        last_key: str | None = None) -> tuple[str, str | None]:
    """Next spoken cue as (text, key). Text is "" when nothing new should be spoken.

    key identifies what was last said; pass it back as last_key so a cue is only spoken
    when it changes. Cues stay under 8 words. beeps_only never speaks.
    """
    if style == "beeps_only":
        return "", last_key
    if not found:
        if lost_s < LOST_AFTER_S or last_key == "lost":
            return "", last_key
        return "Lost it. Turn slowly left.", "lost"
    if distance == "reach":
        return ("" if last_key == "reach" else "Reach out now"), "reach"
    name = item.capitalize()
    if style == "left_right":
        key = _direction(angle)
        text = f"{name} straight ahead" if key == "ahead" else f"{name} {key}"
    else:  # clock_face
        key = f"c{clock}"
        text = f"{name} at {clock} o'clock"
    return ("" if key == last_key else text), key


@dataclass
class GuideState:
    """Per-item tracking between /guide calls. Owned by the caller; times are monotonic seconds."""
    since: float                 # last time the item was seen (or search start)
    last_angle: float | None = None
    last_key: str | None = None
    touched: float = 0.0


@dataclass
class NavigateState:
    """Per-item room-to-room cue tracking. Speak only when the hop or fallback key changes."""
    since: float
    last_key: str | None = None
    touched: float = 0.0


def navigate_say(text: str, key: str, last_key: str | None) -> tuple[str, str]:
    """Return (spoken text, new key); empty text when the navigation cue has not changed."""
    if key == last_key:
        return "", last_key
    return text, key


def compute_guide(item: str, box: list[float] | None, state: GuideState, style: str,
                  fov_h: float, now: float) -> dict:
    """One guidance step from a detection (box or None) to a GuideResponse-shaped dict."""
    state.touched = now
    if box is None:
        text, state.last_key = say(item, None, None, None, style, found=False,
                                   lost_s=now - state.since, last_key=state.last_key)
        return {"found": False, "point": None, "box": None, "angle": None, "clock": None,
                "distance": None, "pan": 0.0, "say": text, "beep_ms": 0}
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    angle = angle_deg(cx, fov_h)
    if state.last_angle is not None and now - state.since <= SMOOTH_WINDOW_S:
        angle = smooth(state.last_angle, angle)
    state.last_angle, state.since = angle, now
    clock, dist = clock_hour(angle), distance_bucket(box, cy)
    text, state.last_key = say(item, angle, clock, dist, style, last_key=state.last_key)
    return {"found": True, "point": [round(cx, 3), round(cy, 3)], "box": box,
            "angle": round(angle, 1), "clock": clock, "distance": dist,
            "pan": round(pan(angle), 2), "say": text, "beep_ms": beep_ms(dist)}
