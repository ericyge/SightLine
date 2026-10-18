"""POST /api/guide: when the item is lost, "Lost it" points the shorter way back to where it was last seen."""
import pytest

from app import main
from tests.test_api import frame


@pytest.fixture()
def clock(monkeypatch):
    """A controllable monotonic clock for the guidance timers (the 2 s before 'Lost it')."""
    now = {"t": 1000.0}
    monkeypatch.setattr(main.time, "monotonic", lambda: now["t"])
    return now


def see(client, jpeg, x, yaw=None):
    data = {"item": "wallet"} if yaw is None else {"item": "wallet", "yaw_deg": str(yaw)}
    r = client.post("/api/guide", files=frame(jpeg), data=data, headers={"X-Mock-Item-X": str(x)})
    assert r.status_code == 200 and r.json()["found"] is True
    return r.json()


def lose(client, jpeg, yaw=None):
    data = {"item": "wallet"} if yaw is None else {"item": "wallet", "yaw_deg": str(yaw)}
    r = client.post("/api/guide", files=frame(jpeg), data=data, headers={"X-Mock-Hide": "true"})
    assert r.status_code == 200 and r.json()["found"] is False
    return r.json()["say"]


def test_lost_on_the_right_says_turn_right(client, jpeg, clock):
    see(client, jpeg, 0.9)                                    # to the right of centre
    clock["t"] += 3
    assert lose(client, jpeg) == "Lost it. Turn slowly right."


def test_lost_on_the_left_says_turn_left(client, jpeg, clock):
    see(client, jpeg, 0.1)
    clock["t"] += 3
    assert lose(client, jpeg) == "Lost it. Turn slowly left."


def test_if_you_turned_past_it_the_cue_points_back(client, jpeg, clock):
    see(client, jpeg, 0.9, yaw=100)                           # 29 degrees to the right when the heading was 100
    clock["t"] += 3
    assert lose(client, jpeg, yaw=150) == "Lost it. Turn slowly left."   # turned right 50: now past it


def test_turned_the_other_way_still_says_right(client, jpeg, clock):
    see(client, jpeg, 0.9, yaw=100)
    clock["t"] += 3
    assert lose(client, jpeg, yaw=70) == "Lost it. Turn slowly right."   # turned left 30: it is further right


def test_never_seen_keeps_the_original_cue(client, jpeg, clock):
    assert lose(client, jpeg) == ""                           # inside the first 2 seconds: nothing yet
    clock["t"] += 3
    assert lose(client, jpeg) == "Lost it. Turn slowly left."


def test_it_is_said_once_and_not_before_two_seconds(client, jpeg, clock):
    see(client, jpeg, 0.9)
    clock["t"] += 1.0
    assert lose(client, jpeg) == ""
    clock["t"] += 1.5
    assert lose(client, jpeg) == "Lost it. Turn slowly right."
    clock["t"] += 1.0
    assert lose(client, jpeg) == ""                           # not repeated while still lost


def test_the_heading_is_optional(client, jpeg, clock):
    see(client, jpeg, 0.9)                                    # the phone sent no heading at all
    clock["t"] += 3
    assert lose(client, jpeg, yaw=250) == "Lost it. Turn slowly right."  # a heading now but none then: side only
