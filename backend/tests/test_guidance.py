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
    text, key = g.navigate_say("Walk forward", "a->b", None)
    assert text == "Walk forward" and key == "a->b"
    assert g.navigate_say("Walk forward", "a->b", key) == ("", "a->b")
    text2, key2 = g.navigate_say("Turn right", "b->c", key)
    assert text2 == "Turn right" and key2 == "b->c"


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
    assert lost["found"] is False and lost["say"] == "Lost it. Turn slowly left."
    assert lost["beep_ms"] == 0
