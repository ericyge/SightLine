"""Step 1: run with pytest -q -s to inspect mock navigation decisions. No real DB or API calls."""
import json
from pathlib import Path

import pytest

from app import db
from app.memory.cli import main as memory_cli

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "two_room_memory.json"


@pytest.fixture
def apartment(client):
    data = json.loads(EXAMPLE.read_text())
    data["places"].append({
        "id": "window_counter", "name": "counter beside the window", "room": "kitchen",
        "description": "A second counter across the kitchen.", "landmarks": ["window", "radiator"],
    })
    assert client.post("/api/memory/map", json=data).status_code == 200
    return client


def remember_wallet(place_id="kitchen_counter", *, room="kitchen", spot="on the counter",
                    ts="2026-10-17T20:00:00Z"):
    return db.add_sighting("wallet", room, spot, [.1, .2, .3, .4], None, ts, "", place_id=place_id)


def directions(client, start="bedroom_door"):
    params = {"item": "wallet"}
    if start is not None:
        params["from_place"] = start
    response = client.get("/api/memory/directions", params=params)
    assert response.status_code == 200
    return response.json()


def guide(client, jpeg, start="bedroom_door", *, visible=False):
    headers = {"X-Mock-Place": start, "X-Mock-Facing": "aligned"}
    if not visible:
        headers["X-Mock-Hide"] = "1"
    response = client.post("/api/guide", data={"item": "wallet"}, headers=headers,
                           files={"frame": ("frame.jpg", jpeg, "image/jpeg")})
    assert response.status_code == 200
    return response.json()


def show(label, response):
    print(f"\n{label}: {response.get('status', response.get('mode'))}")
    print(f"  Spoken response: {response['say'] or '(no repeated cue)'}")


def test_checkpoint_known_route_then_arrival_then_visible_wallet(apartment, jpeg):
    remember_wallet()
    info = directions(apartment)
    assert info["status"] == "ok"
    assert info["path"] == ["bedroom_door", "kitchen_entry", "kitchen_counter"]
    show("Known start and destination", info)
    for start, instruction in [("bedroom_door", "Follow the hallway toward the kitchen entrance."),
                               ("kitchen_entry", "Approach the counter beside the sink.")]:
        result = guide(apartment, jpeg, start)
        assert result["mode"] == "navigate"
        assert result["next_instruction"] == result["say"] == instruction
        show(f"Camera recognized {start}", result)
    arrival = guide(apartment, jpeg, "kitchen_counter")
    assert arrival["mode"] == "navigate" and arrival["next_instruction"] is None
    assert "Look around slowly" in arrival["say"]
    show("At the counter", arrival)
    visible = guide(apartment, jpeg, "kitchen_counter", visible=True)
    assert visible["mode"] == "visual" and visible["found"] and visible["beep_ms"] > 0
    show("Wallet visible again", visible)


@pytest.mark.parametrize("start", [None, "", "missing"])
def test_checkpoint_start_is_never_guessed_from_map_order(apartment, start):
    remember_wallet()
    result = directions(apartment, start)
    assert result["status"] == "unknown_current"
    assert result["current_place"] is None and result["destination"]["id"] == "kitchen_counter"
    assert result["steps"] == result["hops"] == result["path"] == []
    where = apartment.get("/api/where", params={"item": "wallet", "from_place": start or ""}).json()
    assert where["directions"] == [] and where["destination"]["id"] == "kitchen_counter"
    show(f"Starting place {start!r}", result)


@pytest.mark.parametrize("room,spot", [
    ("kitchen", "on the counter"),                         # Two counters: never choose the first.
    ("kitchen", "on counter beside the sink"),             # Even a name in text is not an assignment.
    ("bedroom", "inside a bag"),                          # One mapped place in a room is insufficient.
])
def test_checkpoint_new_unassigned_sighting_blocks_old_destination(apartment, jpeg, room, spot):
    remember_wallet(ts="2026-10-17T19:00:00Z")
    newest = remember_wallet(None, room=room, spot=spot)
    info = directions(apartment)
    assert info["found"] and info["status"] == "unknown_destination"
    assert info["destination"] is None and info["steps"] == info["hops"] == []
    show(f"New sighting: {room}, {spot}", info)
    where = apartment.get("/api/where?item=wallet&from_place=bedroom_door").json()
    assert where["history"][0]["id"] == newest["id"]
    assert where["destination"] is None and where["directions"] == []
    lost = guide(apartment, jpeg)
    assert lost["mode"] == "lost" and lost["destination"] is None
    assert lost["next_instruction"] is None and lost["beep_ms"] == 0
    assert "couldn't identify its place" in lost["say"]
    show("Guidance with unassigned destination", lost)
    assert guide(apartment, jpeg)["say"] == ""  # No speech spam.


def test_checkpoint_clearing_an_assignment_is_respected(apartment, jpeg):
    sighting = remember_wallet(spot="on counter beside the sink")
    assert guide(apartment, jpeg)["mode"] == "navigate"
    url = f"/api/memory/sightings/{sighting['id']}/place"
    assert apartment.put(url, json={"place_id": None}).status_code == 200
    assert directions(apartment)["status"] == "unknown_destination"
    lost = guide(apartment, jpeg)
    assert lost["mode"] == "lost" and lost["destination"] is None
    show("Assignment cleared", lost)
    assert apartment.put(url, json={"place_id": "kitchen_counter"}).status_code == 200
    restored = guide(apartment, jpeg)
    assert restored["mode"] == "navigate" and restored["say"]  # Re-announce after uncertainty.
    show("Assignment confirmed again", restored)


def test_checkpoint_camera_location_unknown_then_recovers(apartment, jpeg):
    remember_wallet()
    assert guide(apartment, jpeg)["mode"] == "navigate"
    lost = guide(apartment, jpeg, "unknown")
    assert lost["mode"] == "lost" and lost["current_place"] is None
    assert lost["next_instruction"] is None and lost["beep_ms"] == 0
    assert "slowly scan" in lost["say"]
    show("Camera cannot locate the user", lost)
    assert guide(apartment, jpeg, "unknown")["say"] == ""
    recovered = guide(apartment, jpeg)
    assert recovered["mode"] == "navigate" and recovered["say"]


def test_checkpoint_return_path_requires_its_own_connection(apartment, jpeg):
    remember_wallet("kitchen_entry")
    info = directions(apartment, "kitchen_counter")
    assert info["status"] == "no_path" and info["steps"] == info["hops"] == []
    show("Only the outward path was recorded", info)
    lost = guide(apartment, jpeg, "kitchen_counter")
    assert lost["mode"] == "lost" and lost["next_instruction"] is None and lost["beep_ms"] == 0
    where = apartment.get("/api/where?item=wallet&from_place=kitchen_counter").json()
    assert where["directions"] == []
    instruction = "Follow the counter toward the kitchen entrance."
    response = apartment.post("/api/memory/map", json={"connections": [{
        "from_place_id": "kitchen_counter", "to_place_id": "kitchen_entry",
        "departure_view": "Facing the entrance from the sink.", "instruction": instruction,
        "arrival_view": "At the kitchen entrance, facing the hallway.",
    }]})
    assert response.status_code == 200
    info = directions(apartment, "kitchen_counter")
    assert info["status"] == "ok" and info["steps"][0] == instruction
    assert info["hops"][0]["inferred_reverse"] is False
    restored = guide(apartment, jpeg, "kitchen_counter")
    assert restored["mode"] == "navigate" and restored["say"] == instruction
    show("Return connection recorded", restored)


def test_checkpoint_never_seen_item_has_no_route(apartment, jpeg):
    result = directions(apartment)
    assert result["status"] == "item_not_seen" and not result["found"]
    assert result["destination"] is None and result["steps"] == []
    assert guide(apartment, jpeg)["mode"] == "lost"
    show("Wallet never seen", result)


def test_checkpoint_cli_requires_start_and_newest_assignment(apartment, capsys):
    remember_wallet()
    assert memory_cli(["directions", "wallet"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "unknown_current"
    assert memory_cli(["directions", "wallet", "--from-place", "bedroom_door"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"
    remember_wallet(None, ts="2026-10-17T21:00:00Z")
    assert memory_cli(["directions", "wallet", "--from-place", "bedroom_door"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "unknown_destination"
    assert memory_cli(["where", "wallet", "--from-place", "bedroom_door"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["destination"] is None and result["directions"] == []
    print("\nCLI: requires a known start and respects the newest assignment.")
