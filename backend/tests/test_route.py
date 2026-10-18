"""Deterministic BFS routing over taught place_connections (no Perceptron)."""
import json
from pathlib import Path

from app import memory
from app.memory.models import MemoryMapUpdate

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "two_room_memory.json"


def teach_two_room():
    data = json.loads(EXAMPLE.read_text())
    return memory.teach_map(MemoryMapUpdate.model_validate(data))


def test_plan_route_shortest_path(client):
    teach_two_room()
    plan = memory.plan_route("bedroom_door", "kitchen_counter")
    assert plan.status == "ok"
    assert plan.path == ["bedroom_door", "kitchen_entry", "kitchen_counter"]
    assert plan.next_hop is not None
    assert plan.next_hop.from_place_id == "bedroom_door"
    assert plan.next_hop.to_place_id == "kitchen_entry"
    assert plan.next_hop.instruction == "Follow the hallway toward the kitchen entrance."
    assert len(plan.hops) == 2
    assert plan.hops[1].instruction == "Approach the counter beside the sink."


def test_plan_route_already_there(client):
    teach_two_room()
    plan = memory.plan_route("kitchen_counter", "kitchen_counter")
    assert plan.status == "already_there"
    assert plan.path == ["kitchen_counter"]
    assert plan.next_hop is None


def test_plan_route_unknown_and_no_path(client):
    teach_two_room()
    assert memory.plan_route(None, "kitchen_counter").status == "unknown_current"
    assert memory.plan_route("missing", "kitchen_counter").status == "unknown_current"
    assert memory.plan_route("bedroom_door", None).status == "unknown_destination"
    assert memory.plan_route("bedroom_door", "missing").status == "unknown_destination"
    # Directed edges only: reverse path is not invented.
    assert memory.plan_route("kitchen_counter", "bedroom_door").status == "no_path"


def test_plan_route_accepts_explicit_graph(client):
    places = [{"id": "a", "name": "A", "room": "r", "description": "A.", "landmarks": ["x", "y"]},
              {"id": "b", "name": "B", "room": "r", "description": "B.", "landmarks": ["x", "y"]}]
    connections = [{"from_place_id": "a", "to_place_id": "b", "departure_view": "from a",
                    "instruction": "walk to b", "arrival_view": "at b"}]
    plan = memory.plan_route("a", "b", connections=connections, places=places)
    assert plan.status == "ok" and plan.next_hop.instruction == "walk to b"


def test_directions_to_item_uses_saved_forward_route(client):
    from app import db
    teach_two_room()
    db.add_sighting("keys", "kitchen", "on the counter", [.1, .2, .3, .4], None,
                    "2026-10-17T20:00:00Z", "", place_id="kitchen_counter")
    info = memory.directions_to_item("keys", from_place_id="bedroom_door")
    assert info["status"] == "ok"
    assert len(info["steps"]) == 3
    assert info["steps"][0] == "Follow the hallway toward the kitchen entrance."
    assert info["steps"][1] == "Approach the counter beside the sink."
    assert "Arrive at counter beside the sink" in info["steps"][2]
    assert len(info["hops"]) == 2
    assert info["hops"][0]["to_place_id"] == "kitchen_entry"
    assert info["hops"][1]["to_place_id"] == "kitchen_counter"
    assert all(hop["inferred_reverse"] is False for hop in info["hops"])

    res = client.get("/api/memory/directions", params={"item": "keys", "from_place": "bedroom_door"})
    assert res.status_code == 200
    assert res.json()["steps"] == info["steps"]
    where_res = client.get("/api/where", params={"item": "keys", "from_place": "bedroom_door"}).json()
    assert where_res["directions"] == info["steps"]
