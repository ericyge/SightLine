"""Room-to-room navigate mode on /api/guide (mock vision, taught map)."""
import json
from pathlib import Path

from app import db

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "two_room_memory.json"


def frame(jpeg):
    return {"frame": ("frame.jpg", jpeg, "image/jpeg")}


def teach(client):
    data = json.loads(EXAMPLE.read_text())
    assert client.post("/api/memory/map", json=data).status_code == 200


def assign_keys(client, place_id="kitchen_counter"):
    sid = db.add_sighting("keys", "kitchen", "on the counter", [.1, .2, .3, .4], None,
                          "2026-10-17T20:00:00Z", "", place_id=place_id)["id"]
    return sid


def test_guide_navigate_speaks_next_hop(client, jpeg):
    teach(client)
    assign_keys(client)
    headers = {"X-Mock-Facing": "aligned", "X-Mock-Hide": "1", "X-Mock-Place": "bedroom_door"}
    first = client.post("/api/guide", files=frame(jpeg), data={"item": "keys"}, headers=headers).json()
    assert first["mode"] == "navigate"
    assert first["found"] is False
    assert first["beep_ms"] == 0
    assert first["current_place"] == "bedroom_door"
    assert first["destination"]["id"] == "kitchen_counter"
    assert first["next_instruction"] == "Follow the hallway toward the kitchen entrance."
    assert first["say"] == first["next_instruction"]
    second = client.post("/api/guide", files=frame(jpeg), data={"item": "keys"}, headers=headers).json()
    assert second["say"] == "" and second["mode"] == "navigate"


def test_guide_navigate_advances_when_place_changes(client, jpeg):
    teach(client)
    assign_keys(client)
    r1 = client.post("/api/guide", files=frame(jpeg), data={"item": "keys"},
                     headers={"X-Mock-Facing": "aligned", "X-Mock-Hide": "1", "X-Mock-Place": "bedroom_door"}).json()
    assert "hallway" in r1["say"]
    r2 = client.post("/api/guide", files=frame(jpeg), data={"item": "keys"},
                     headers={"X-Mock-Facing": "aligned", "X-Mock-Hide": "1", "X-Mock-Place": "kitchen_entry"}).json()
    assert r2["current_place"] == "kitchen_entry"
    assert r2["say"] == "Approach the counter beside the sink."


def test_guide_at_destination_without_item(client, jpeg):
    teach(client)
    assign_keys(client)
    r = client.post("/api/guide", files=frame(jpeg), data={"item": "keys"},
                    headers={"X-Mock-Facing": "aligned", "X-Mock-Hide": "1", "X-Mock-Place": "kitchen_counter"}).json()
    assert r["mode"] == "navigate"
    assert r["current_place"] == "kitchen_counter"
    assert "Look around slowly" in r["say"]
    assert r["next_instruction"] is None


def test_guide_fallback_when_unlocalized(client, jpeg):
    teach(client)
    assign_keys(client)
    r = client.post("/api/guide", files=frame(jpeg), data={"item": "keys"},
                    headers={"X-Mock-Hide": "1"}).json()
    assert r["mode"] == "lost"
    assert r["destination"]["id"] == "kitchen_counter"
    assert r["current_place"] is None
    assert "can't tell where you are" in r["say"]


def test_guide_visual_still_works_when_item_visible(client, jpeg):
    teach(client)
    assign_keys(client)
    r = client.post("/api/guide", files=frame(jpeg), data={"item": "keys"},
                    headers={"X-Mock-Item-X": "0.8"}).json()
    assert r["mode"] == "visual"
    assert r["found"] is True
    assert r["destination"]["id"] == "kitchen_counter"
    assert r["beep_ms"] == 350
