"""Persistent places, explicit assignments, migrations, and honest last-seen recall."""
import json
import sqlite3

import pytest

from app import db, memory
from app.memory.cli import main as memory_cli
from app.memory.models import MemoryMapUpdate


def place(id="kitchen_counter", room="kitchen", name="counter beside the sink"):
    return {"id": id, "room": room, "name": name, "description": "Counter beside the sink.",
            "landmarks": ["sink", "faucet"]}


def edge(start="bedroom_door", end="kitchen_counter"):
    return {"from_place_id": start, "to_place_id": end,
            "departure_view": "At the bedroom door facing the hallway.",
            "instruction": "Follow the hallway to the kitchen counter.",
            "arrival_view": "Facing the kitchen sink."}


def teach(client):
    result = client.post("/api/memory/map", json={"places": [place()]})
    assert result.status_code == 200


def sighting(ts="2026-10-17T19:00:00Z", place_id=None, room="kitchen"):
    return db.add_sighting("wallet", room, "on the counter", [.1, .2, .3, .4], None,
                           ts, "", place_id=place_id)


def test_map_roundtrip_merge_and_persistence(client):
    assert client.get("/api/memory/map").json() == {"places": [], "connections": []}
    update = {"places": [place(), place("bedroom_door", "bedroom", "bedroom doorway")],
              "connections": [edge()]}
    assert client.post("/api/memory/map", json=update).status_code == 200
    updated_place = place(name="counter to the right of the sink")
    assert client.post("/api/memory/map", json={"places": [updated_place]}).status_code == 200
    # Reinitialize schema as on process restart: keep the complete map and stable IDs.
    db._initialized.discard(db.db_path())
    saved = client.get("/api/memory/map").json()
    assert len(saved["places"]) == 2
    assert saved["places"][1] == updated_place
    assert saved["connections"] == [edge()]  # No automatically invented reverse direction.
    changed_edge = edge() | {"instruction": "Follow the hallway past the cupboard."}
    assert client.post("/api/memory/map", json={"connections": [changed_edge]}).json()["connections"] == [changed_edge]


def test_invalid_import_is_atomic(client):
    teach(client)
    result = client.post("/api/memory/map", json={
        "places": [place("new_place", "bedroom"), place(room="bedroom")]
    })
    assert result.status_code == 409
    assert client.get("/api/memory/map").json()["places"] == [place()]
    result = client.post("/api/memory/map", json={
        "places": [place("bedroom_door", "bedroom")], "connections": [edge(end="unknown")]
    })
    assert result.status_code == 404
    assert client.get("/api/memory/map").json()["places"] == [place()]


def test_map_can_grow_across_bounded_imports(client):
    batch = [place(id=f"place_{i}") for i in range(100)]
    assert client.post("/api/memory/map", json={"places": batch + [place()]}).status_code == 422
    assert client.post("/api/memory/map", json={"places": batch}).status_code == 200
    teach(client)
    assert len(client.get("/api/memory/map").json()["places"]) == 101


@pytest.mark.parametrize("payload", [
    {"places": [place(), place()]},
    {"connections": [edge(), edge()]},
    {"connections": [edge("same", "same")]},
    {"places": [place("not a stable ID")]},
    {"places": [place(name=" ")]},
    {"places": [place() | {"landmarks": [" "]}]},
    {"places": [place() | {"unexpected": True}]},
])
def test_bad_map_shape_rejected(client, payload):
    assert client.post("/api/memory/map", json=payload).status_code == 422
    assert client.get("/api/memory/map").json()["places"] == []


def test_observe_links_destination_and_where_uses_alias(client, jpeg):
    teach(client)
    result = client.post("/api/observe", files={"frame": ("frame.jpg", jpeg, "image/jpeg")},
                         data={"room": "kitchen", "place_id": "kitchen_counter"})
    assert result.status_code == 200
    assert all(s["id"] > 0 and s["place_id"] == "kitchen_counter" for s in result.json()["sightings"])
    answer = client.get("/api/where?item=my%20wallet").json()
    assert answer["destination"] == place()
    assert "Last seen at counter beside the sink" in answer["say"]
    assert answer["history"][0]["place_id"] == "kitchen_counter"


@pytest.mark.parametrize("place_id,room,status", [
    ("unknown", "kitchen", 404), ("kitchen_counter", "bedroom", 409),
    ("invalid ID", "kitchen", 422)
])
def test_bad_place_rejected_before_vision_or_crops(client, jpeg, monkeypatch, place_id, room, status):
    from app import main
    teach(client)
    def unexpected():
        pytest.fail("Invalid assignment must not invoke the model")
    monkeypatch.setattr(main, "get_vision", unexpected)
    result = client.post("/api/observe", files={"frame": ("frame.jpg", jpeg, "image/jpeg")},
                         data={"room": room, "place_id": place_id})
    assert result.status_code == status
    assert not list(db.thumbs_dir().glob("*.jpg"))
    assert db.recent_sightings("wallet") == []


def test_assign_and_clear_existing_sighting_preserves_observation(client):
    teach(client)
    old = sighting()
    url = f"/api/memory/sightings/{old['id']}/place"
    linked = client.put(url, json={"place_id": "kitchen_counter"})
    assert linked.status_code == 200
    assert linked.json() == old | {"place_id": "kitchen_counter", "place_source": "manual",
                                  "place_reason": "Manually assigned."}
    db._initialized.discard(db.db_path())
    assert client.get("/api/where?item=wallet").json()["destination"] == place()
    cleared = client.put(url, json={"place_id": None})
    assert cleared.json() == old | {"place_reason": "Manually cleared."}
    assert client.get("/api/where?item=wallet").json()["destination"] is None


def test_assignment_errors_do_not_modify_observation(client):
    teach(client)
    old = sighting(room="bedroom")
    url = f"/api/memory/sightings/{old['id']}/place"
    assert client.put(url, json={"place_id": "unknown"}).status_code == 404
    assert client.put(url, json={"place_id": "kitchen_counter"}).status_code == 409
    assert client.put(url, json={}).status_code == 422
    assert client.put("/api/memory/sightings/999/place", json={"place_id": None}).status_code == 404
    assert db.recent_sightings("wallet") == [old]


def test_new_unmapped_sighting_does_not_reuse_older_destination(client):
    teach(client)
    old = sighting(place_id="kitchen_counter")
    newest = sighting("2026-10-17T20:00:00Z", room="bedroom")
    # Importing an older frame afterwards must not make it the latest memory.
    sighting("2026-10-17T11:30:00-07:00", place_id="kitchen_counter")
    answer = client.get("/api/where?item=wallet").json()
    assert answer["found"] and answer["destination"] is None
    assert answer["history"][0] == newest
    assert answer["history"][1]["id"] == old["id"]
    items = client.get("/api/memory/items").json()
    assert next(i for i in items if i["item"] == "wallet") == {
        "item": "wallet", "last": newest, "destination": None}
    assert next(i for i in items if i["item"] == "keys")["last"] is None


def test_empty_detection_preserves_last_seen_place(client, jpeg, monkeypatch):
    from app import main
    teach(client)
    last = sighting(place_id="kitchen_counter")
    monkeypatch.setattr(main.get_vision(), "detect", lambda *args: [])
    result = client.post("/api/observe", files={"frame": ("frame.jpg", jpeg, "image/jpeg")},
                         data={"room": "bedroom"})
    assert result.json() == {"sightings": []}
    answer = client.get("/api/where?item=wallet").json()
    assert answer["history"][0] == last and answer["destination"] == place()


def test_default_home_spot_does_not_create_a_destination(client):
    answer = client.get("/api/where?item=wallet").json()
    assert not answer["found"] and answer["destination"] is None
    assert client.get("/api/memory/map").json()["places"] == []


def test_existing_nullable_database_migrates_without_losing_history(tmp_path, monkeypatch):
    path = tmp_path / "prior.db"
    with sqlite3.connect(path) as c:
        c.executescript(db.SCHEMA)
        # Existing offset timestamps must sort chronologically, not lexicographically.
        c.execute("INSERT INTO sightings VALUES (4,'wallet','kitchen','counter',.1,.2,.3,.4,"
                  "NULL,'2026-10-17T12:00:00-07:00','/api/thumbs/old.jpg')")
        c.execute("INSERT INTO sightings VALUES (5,'wallet','bedroom','desk',.1,.2,.3,.4,"
                  "NULL,'2026-10-17T18:00:00+00:00','')")
    monkeypatch.setenv("DB_PATH", str(path))
    latest = db.recent_sightings("wallet")[0]
    assert latest["id"] == 4 and latest["place_id"] is None
    assert latest["confidence"] is None and latest["thumb"] == "/api/thumbs/old.jpg"
    memory.teach_map(MemoryMapUpdate(places=[place()]))
    memory.assign_sighting(4, "kitchen_counter")
    db._initialized.discard(db.db_path())
    assert memory.destination_for(db.recent_sightings("wallet")[0]) == place()
    assert sighting()["id"] == 6


def test_memory_cli_teaches_queries_and_assigns_without_model(client, tmp_path, capsys, monkeypatch):
    # Memory works without credentials even if real vision is configured.
    monkeypatch.setenv("PERCEPTRON_MODE", "perceptron")
    monkeypatch.setenv("PERCEPTRON_API_KEY", "")
    map_file = tmp_path / "map.json"
    map_file.write_text(json.dumps({"places": [place()]}))
    assert memory_cli(["teach", str(map_file)]) == 0
    assert json.loads(capsys.readouterr().out)["places"] == [place()]
    last = sighting()
    assert memory_cli(["assign", str(last["id"]), "--place-id", "kitchen_counter"]) == 0
    assert json.loads(capsys.readouterr().out)["place_id"] == "kitchen_counter"
    assert memory_cli(["where", "my wallet"]) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["destination"] == place() and answer["found"] and answer["watched"]
    assert memory_cli(["assign", str(last["id"]), "--clear"]) == 0
    assert json.loads(capsys.readouterr().out)["place_id"] is None
    assert memory_cli(["where", "unicorn"]) == 0
    answer = json.loads(capsys.readouterr().out)
    assert not answer["found"] and not answer["watched"]
    assert memory_cli(["assign", "999", "--place-id", "kitchen_counter"]) == 1
    assert "Unknown sighting" in capsys.readouterr().err


def test_example_map_is_valid():
    from pathlib import Path
    path = Path(__file__).parents[1] / "examples" / "two_room_memory.json"
    example = MemoryMapUpdate.model_validate_json(path.read_text())
    ids = {p.id for p in example.places}
    assert all(e.from_place_id in ids and e.to_place_id in ids for e in example.connections)
