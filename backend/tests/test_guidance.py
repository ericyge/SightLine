import pytest

from app import guidance as g


def test_angle_deg():
    assert g.angle_deg(0.5, 65) == 0
    assert g.angle_deg(1.0, 65) == pytest.approx(32.5)
    assert g.angle_deg(0.0, 65) == pytest.approx(-32.5)
    assert g.angle_deg(0.71, 65) == pytest.approx(13.65)


@pytest.mark.parametrize("angle,hour", [(0, 12), (30, 1), (60, 2), (-30, 11), (-60, 10),
                                         (10, 12), (-10, 12), (20, 1), (180, 6), (-180, 6),
                                         (360, 12), (15, 1), (-15, 12)])
def test_clock_hour(angle, hour):
    assert g.clock_hour(angle) == hour


@pytest.mark.parametrize("box,cy,expected", [
    ([0.4, 0.5, 0.5, 0.59], 0.55, "far"),
    ([0.4, 0.5, 0.5, 0.6], 0.55, "near"),       # h == 0.10 is near
    ([0.4, 0.4, 0.5, 0.7], 0.55, "near"),       # h == 0.30 is near
    ([0.4, 0.4, 0.5, 0.8], 0.6, "reach"),       # big and low
    ([0.4, 0.1, 0.5, 0.5], 0.3, "near"),        # big but high in frame
    ([0.4, 0.3, 0.5, 0.7], 0.5, "near"),        # big, center_y == 0.5 is not > 0.5
])
def test_distance_bucket(box, cy, expected):
    assert g.distance_bucket(box, cy) == expected


def test_pan_clamps():
    assert g.pan(0) == 0
    assert g.pan(15) == 0.5
    assert g.pan(30) == 1
    assert g.pan(90) == 1
    assert g.pan(-90) == -1


def test_beep_ms():
    assert (g.beep_ms("far"), g.beep_ms("near"), g.beep_ms("reach")) == (800, 350, 150)


def test_pitch_boost():
    assert g.pitch_boost(0) and g.pitch_boost(8) and g.pitch_boost(-8)
    assert not g.pitch_boost(8.1) and not g.pitch_boost(-20)


def test_smooth():
    assert g.smooth(10, 20) == 15
    assert g.smooth(-10, 10) == 0
    assert g.smooth(5, 5) == 5


def test_yaw_compensate():
    assert g.yaw_compensate(20, 110, 100) == 10      # turned right 10 deg: item is 10 deg closer
    assert g.yaw_compensate(20, 90, 100) == 30       # turned left
    assert g.yaw_compensate(20, 100, 100) == 20
    assert g.yaw_compensate(20, 5, 355) == 10        # wraps across 0/360
    assert g.yaw_compensate(-5, 355, 5) == 5


def test_say_clock_face_only_on_change():
    text, key = g.say("wallet", 60, 2, "far", "clock_face")
    assert text == "Wallet at 2 o'clock" and key == "c2"
    assert g.say("wallet", 61, 2, "far", "clock_face", last_key=key) == ("", "c2")
    assert g.say("wallet", 30, 1, "far", "clock_face", last_key=key)[0] == "Wallet at 1 o'clock"


def test_say_left_right():
    assert g.say("wallet", 0, 12, "far", "left_right")[0] == "Wallet straight ahead"
    assert g.say("wallet", 15, 1, "far", "left_right")[0] == "Wallet slightly right"
    assert g.say("wallet", -15, 11, "far", "left_right")[0] == "Wallet slightly left"
    assert g.say("wallet", 28, 1, "far", "left_right")[0] == "Wallet turn right"
    assert g.say("wallet", -28, 11, "far", "left_right")[0] == "Wallet turn left"


def test_say_reach_once():
    text, key = g.say("wallet", 3, 12, "reach", "clock_face", last_key="c12")
    assert text == "Reach out now" and key == "reach"
    assert g.say("wallet", 3, 12, "reach", "clock_face", last_key="reach")[0] == ""


def test_say_beeps_only_is_silent():
    assert g.say("wallet", 40, 1, "reach", "beeps_only")[0] == ""
    assert g.say("wallet", None, None, None, "beeps_only", found=False, lost_s=5)[0] == ""


def test_say_lost_after_two_seconds_once():
    assert g.say("wallet", None, None, None, "clock_face", found=False, lost_s=1.9)[0] == ""
    text, key = g.say("wallet", None, None, None, "clock_face", found=False, lost_s=2.0)
    assert text == "Lost it. Turn slowly left." and key == "lost"
    assert g.say("wallet", None, None, None, "clock_face", found=False, lost_s=3, last_key="lost")[0] == ""


def test_say_cues_are_short():
    for style in ("clock_face", "left_right"):
        for angle, clock in ((-30, 11), (0, 12), (30, 1)):
            assert len(g.say("medication", angle, clock, "far", style)[0].split()) < 8


def test_navigate_say_only_on_change():
    text, key, spoken_at = g.navigate_say("Walk forward", "a->b", None)
    assert text == "Walk forward" and key == "a->b" and spoken_at == 0.0
    assert g.navigate_say("Walk forward", "a->b", key) == ("", "a->b", 0.0)
    text2, key2, _ = g.navigate_say("Turn right", "b->c", key)
    assert text2 == "Turn right" and key2 == "b->c"


def test_navigate_say_repeats_after_interval():
    text, key, at = g.navigate_say("Turn left", "hop:left", None, now=1.0, last_spoken_at=0.0)
    assert text == "Turn left" and key == "hop:left" and at == 1.0
    assert g.navigate_say("Turn left", "hop:left", key, now=5.0, last_spoken_at=at) == ("", key, at)
    again, key2, at2 = g.navigate_say("Turn left", "hop:left", key, now=16.0, last_spoken_at=at)
    assert again == "Turn left" and key2 == key and at2 == 16.0


def test_compute_guide_found_matches_contract_example():
    state = g.GuideState(since=0.0)
    r = g.compute_guide("wallet", [0.66, 0.55, 0.77, 0.69], state, "left_right", 65, now=1.0)
    assert r["found"] and r["point"] == [0.715, 0.62]
    assert r["angle"] == 14.0
    assert r["clock"] == 12 and r["distance"] == "near" and r["beep_ms"] == 350
    assert r["pan"] == pytest.approx(0.46, abs=0.01)


def test_compute_guide_smooths_then_loses():
    state = g.GuideState(since=0.0)
    g.compute_guide("wallet", [0.4, 0.5, 0.5, 0.65], state, "clock_face", 65, now=1.0)   # x=.45
    r = g.compute_guide("wallet", [0.8, 0.5, 0.9, 0.65], state, "clock_face", 65, now=2.0)  # x=.85
    assert r["angle"] == pytest.approx(0.5 * 22.75 + 0.5 * -3.25, abs=0.1)
    assert g.compute_guide("wallet", None, state, "clock_face", 65, now=3.0)["say"] == ""
    lost = g.compute_guide("wallet", None, state, "clock_face", 65, now=4.1)
    # it was last seen to the right (smoothed angle +9.75), so turning right is the shorter way back
    assert lost["found"] is False and lost["say"] == "Lost it. Turn slowly right."
    assert lost["beep_ms"] == 0


# ---- which way to turn when the item is lost ----

def test_turn_direction_follows_where_the_item_was_last_seen():
    assert g.turn_direction(None, None, None) == "left"          # never seen: the original cue
    assert g.turn_direction(15.0, None, None) == "right"
    assert g.turn_direction(-15.0, None, None) == "left"
    assert g.turn_direction(0.0, None, None) == "left"           # dead ahead and lost: nothing better to say


def test_turn_direction_accounts_for_how_far_the_phone_has_turned():
    # seen 20 degrees to the right when the heading was 100
    assert g.turn_direction(20, 100, 100) == "right"             # not moved
    assert g.turn_direction(20, 100, 130) == "left"              # turned right 30, past it: come back left
    assert g.turn_direction(20, 100, 70) == "right"              # turned left 30: it is now 50 to the right
    assert g.turn_direction(-20, 100, 60) == "right"             # turned left 40, past it: it is now 20 to the right
    assert g.turn_direction(-20, 100, 130) == "left"             # turned right 30: it is now 50 to the left


def test_turn_direction_takes_the_short_way_across_north_and_behind_you():
    assert g.turn_direction(20, 350, 10) == "left"               # heading 350 -> 10 is a 20 degree right turn: it is now 0 or just left
    assert g.turn_direction(20, 350, 30) == "left"               # turned right 40 across north, past it
    assert g.turn_direction(-20, 10, 340) == "right"             # turned left 30 across north, past it
    assert g.turn_direction(20, 100, 280) == "left"              # turned 180: the item is behind, 160 to the left
    assert g.turn_direction(-20, 100, 280) == "right"            # ... and 160 to the right for an item that was on the left
    assert g.turn_direction(10, 100, 100 + 179) == "left"        # turned right 179: item is 169 to the left
    assert g.turn_direction(10, 100, 100 - 179) == "left"        # turned left 179: item is 189 to the right = 171 to the left


def test_turn_direction_ignores_a_missing_heading():
    assert g.turn_direction(20, None, 130) == "right"            # no heading when it was seen
    assert g.turn_direction(20, 100, None) == "right"            # no heading now
    assert g.turn_direction(-20, None, None) == "left"


def test_say_lost_uses_the_turn_it_is_given():
    left = g.say("wallet", None, None, None, "clock_face", found=False, lost_s=2.0)
    right = g.say("wallet", None, None, None, "left_right", found=False, lost_s=2.0, turn="right")
    assert left == ("Lost it. Turn slowly left.", "lost")
    assert right == ("Lost it. Turn slowly right.", "lost")
    assert len(right[0].split()) < 8
    assert g.say("wallet", None, None, None, "beeps_only", found=False, lost_s=5, turn="right")[0] == ""


def test_compute_guide_lost_points_the_shorter_way():
    def lose(box, yaw_seen, yaw_now):
        state = g.GuideState(since=0.0)
        g.compute_guide("wallet", box, state, "clock_face", 65, now=1.0, yaw=yaw_seen)
        return g.compute_guide("wallet", None, state, "clock_face", 65, now=3.5, yaw=yaw_now)["say"]

    right_box = [0.8, 0.5, 0.9, 0.65]                            # x=.85 -> +22.75 degrees (right)
    left_box = [0.1, 0.5, 0.2, 0.65]                             # x=.15 -> -22.75 degrees (left)
    assert lose(right_box, None, None) == "Lost it. Turn slowly right."
    assert lose(left_box, None, None) == "Lost it. Turn slowly left."
    assert lose(right_box, 100, 100) == "Lost it. Turn slowly right."
    assert lose(right_box, 100, 140) == "Lost it. Turn slowly left."    # turned right 40, past it
    assert lose(left_box, 100, 60) == "Lost it. Turn slowly right."     # turned left 40, past it
    state = g.GuideState(since=0.0)
    assert g.compute_guide("wallet", None, state, "clock_face", 65, now=3.0)["say"] == "Lost it. Turn slowly left."


def test_compute_guide_remembers_the_heading_of_the_last_sighting():
    state = g.GuideState(since=0.0)
    g.compute_guide("wallet", [0.4, 0.5, 0.5, 0.65], state, "clock_face", 65, now=1.0, yaw=123.0)
    assert state.yaw_at_last == 123.0
    g.compute_guide("wallet", [0.4, 0.5, 0.5, 0.65], state, "clock_face", 65, now=1.2)
    assert state.yaw_at_last is None                             # an update without a heading clears the old one
