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
