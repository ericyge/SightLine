"""Automatic assignments must stay per-object, evidence-based, and reversible."""
import sqlite3

import pytest

from app import db, memory
from app.memory.models import MemoryMapUpdate
from app.memory.observation import observe_and_save, place_context
from app.vision import Detection
from app.vision.places import PlaceEvidence

TS = "2026-10-17T19:00:00+00:00"
ANCHORS = ["Steel sink inset to the left of the counter", "Fixed white cabinets above the counter"]


def evidence(**changes):
    return PlaceEvidence(**({"decision": "new", "local_key": "counter", "candidate_id": None,
        "name": "counter beside the sink", "room": "kitchen", "description": "White counter between sink and cabinets.",
        "landmarks": ANCHORS, "matched_landmarks": [], "reason": "Distinctive fixed landmark arrangement."} | changes))


def detection(item="wallet", location=None):
    return Detection(item, [.1, .2, .3, .4], None, "on the counter", location)


class StubVision:
    mock = False

    def __init__(self, labels):
        self.labels = labels
        self.contexts = []

    def observe(self, frame, items, room, context, hints=None):
        self.contexts.append(context)
        return self.labels(context) if callable(self.labels) else self.labels

    def detect(self, *args):
        return self.labels


def save(client, jpeg, location=None, **kwargs):
    vision = StubVision([detection(location=location)])
    result, _ = observe_and_save(vision, jpeg, kwargs.pop("room", None), kwargs.pop("ts", TS), **kwargs)
    return result[0]


def teach(id="counter", room="kitchen", landmarks=None):
    memory.teach_map(MemoryMapUpdate(places=[{"id": id, "room": room,
        "name": "counter beside the sink", "description": "White counter between sink and cabinets.",
        "landmarks": ANCHORS if landmarks is None else landmarks}]))


def test_discover_without_setup_and_match_on_later_visit(client, jpeg):
    first = save(client, jpeg, evidence())
    assert first["place_id"].startswith("auto_") and first["place_source"] == "automatic"
    assert first["room"] == "kitchen" and first["confidence"] is None
    assert len(memory.get_map()["places"]) == 1
    db._initialized.discard(db.db_path())
    matched = evidence(decision="match", candidate_id=first["place_id"], matched_landmarks=[0, 1],
                       name="worktop beside the basin", local_key="worktop")
    again = save(client, jpeg, matched, ts="2026-10-17T20:00:00Z")
    assert again["place_id"] == first["place_id"]
    assert len(memory.get_map()["places"]) == 1
    answer = client.get("/api/where?item=my%20wallet").json()
    assert answer["destination"]["id"] == first["place_id"]
    assert answer["history"][0]["place_source"] == "automatic"


def test_each_object_gets_its_own_surface_and_shared_surfaces_reuse_id(client, jpeg):
    counter = evidence()
    desk = evidence(local_key="desk", name="desk under the window", room="study",
                    landmarks=["Tall arched window above the desk", "Built-in bookcase beside the desk"])
    labels = [detection("wallet", counter), detection("keys", counter), detection("phone", desk)]
    saved, _ = observe_and_save(StubVision(labels), jpeg, None, TS)
    assert saved[0]["place_id"] == saved[1]["place_id"] != saved[2]["place_id"]
    assert saved[0]["room"] == "kitchen" and saved[2]["room"] == "study"
    assert len(memory.get_map()["places"]) == 2
    assert memory.get_map()["connections"] == []


@pytest.mark.parametrize("location", [
    None, evidence(decision="unknown", reason="Surface obscured."),
    evidence(landmarks=["sink"]), evidence(landmarks=["sink", " SINK "]),
    evidence(name=None), evidence(local_key=None),
    evidence(decision="match", candidate_id="invented", matched_landmarks=[0, 1]),
    evidence(candidate_id="invented"), evidence(matched_landmarks=[0, 1]),
])
def test_incomplete_or_ambiguous_place_keeps_detection_without_link(client, jpeg, location):
    saved = save(client, jpeg, location)
    assert saved["item"] == "wallet" and saved["place_id"] is None
    assert saved["place_source"] == "none" and saved["place_reason"]
    assert client.get(saved["thumb"]).status_code == 200
    assert memory.get_map()["places"] == []


def test_unknown_room_does_not_require_user_label(client, jpeg):
    saved = save(client, jpeg, evidence(room=None))
    assert saved["place_id"] and saved["room"] == "unknown room"
    assert "unknown room" not in client.get("/api/where?item=wallet").json()["say"]


@pytest.mark.parametrize("indices", [[0], [0, 0], [0, 3]])
def test_matching_requires_two_distinct_supplied_landmarks(client, jpeg, indices):
    teach()
    saved = save(client, jpeg, evidence(decision="match", candidate_id="counter", matched_landmarks=indices))
    assert saved["place_id"] is None


def test_identical_landmarks_at_multiple_places_are_ambiguous(client, jpeg):
    teach("counter_a")
    teach("counter_b")
    saved = save(client, jpeg, evidence(decision="match", candidate_id="counter_a", matched_landmarks=[0, 1]))
    assert saved["place_id"] is None
    assert "distinguish" in saved["place_reason"]


def test_explicit_room_hint_limits_candidates_and_wins_over_inferred_room(client, jpeg):
    teach("counter", room="bedroom")
    saved = save(client, jpeg, evidence(), room="test_room")
    assert saved["room"] == "test_room" and saved["place_id"] != "counter"
    bad = save(client, jpeg, evidence(decision="match", candidate_id="counter", matched_landmarks=[0, 1]),
               room="test_room")
    assert bad["place_id"] is None


def test_newer_carried_object_falls_back_to_older_mapped_destination(client, jpeg):
    first = save(client, jpeg, evidence())
    last = save(client, jpeg, None, ts="2026-10-17T20:00:00Z")
    answer = client.get("/api/where?item=wallet").json()
    assert answer["destination"]["id"] == first["place_id"] and answer["history"][0]["id"] == last["id"]
    assert answer["history"][0]["place_id"] is None
    assert len(memory.get_map()["places"]) == 1


def test_do_not_create_duplicate_place_from_repeated_new_claim(client, jpeg):
    save(client, jpeg, evidence())
    second = save(client, jpeg, evidence(name="different wording"))
    assert second["place_id"] is None and len(memory.get_map()["places"]) == 1


@pytest.mark.parametrize("same_key", [True, False])
def test_conflicting_claims_within_a_frame_are_unresolved(client, jpeg, same_key):
    teach()
    one = evidence(decision="match", candidate_id="counter", matched_landmarks=[0, 1])
    two = one.model_copy(update={"description": "Conflicting description"} if same_key else {"local_key": "other"})
    labels = [detection("wallet", one), detection("keys", two)]
    saved, _ = observe_and_save(StubVision(labels), jpeg, None, TS)
    assert all(s["place_id"] is None for s in saved)


def test_temporal_context_is_session_scoped_recent_and_not_future(client, jpeg):
    first = save(client, jpeg, evidence(), session_id="walk_a")
    context = place_context(None, "walk_a", "2026-10-17T19:00:05+00:00")
    assert context["recent_observations"][0]["place_id"] == first["place_id"]
    for session, ts in [("walk_b", "2026-10-17T19:00:05Z"),
                        ("walk_a", "2026-10-17T19:00:21Z"), ("walk_a", "2026-10-17T18:59:59Z")]:
        assert place_context(None, session, ts)["recent_observations"] == []
    assert place_context(None, None, TS)["recent_observations"] == []


def test_catalog_changes_during_inference_do_not_make_wrong_links(client, jpeg):
    def changed(context):
        teach("another_place", landmarks=["Window beyond the table", "Doorway beside the table"])
        return [detection(location=evidence())]
    saved, _ = observe_and_save(StubVision(changed), jpeg, None, TS)
    assert saved[0]["place_id"] is None and len(memory.get_map()["places"]) == 1


def test_stale_candidate_update_during_inference_is_rejected(client, jpeg):
    teach()
    def changed(context):
        teach(landmarks=["Red brick wall", "Green door"])
        return [detection(location=evidence(decision="match", candidate_id="counter", matched_landmarks=[0, 1]))]
    saved, _ = observe_and_save(StubVision(changed), jpeg, None, TS)
    assert saved[0]["place_id"] is None and "changed" in saved[0]["place_reason"]


def test_large_catalog_is_bounded_and_new_places_are_deferred(client, jpeg):
    for i in range(41):
        teach(f"place_{i}", landmarks=[f"Fixed feature {i}", f"Another fixed feature {i}"])
    context = place_context(None, None, TS)
    assert len(context["places"]) == 40 and not context["catalog_complete"]
    saved = save(client, jpeg, evidence())
    assert saved["place_id"] is None and len(memory.get_map()["places"]) == 41


def test_frame_transaction_rolls_back_places_sightings_and_crops(client, jpeg, monkeypatch):
    actual = db.save_thumb
    calls = []
    def save_crop(*args):
        if calls:
            raise OSError("Simulated storage failure")
        calls.append(True)
        return actual(*args)
    monkeypatch.setattr(db, "save_thumb", save_crop)
    labels = [detection("wallet", evidence()), detection("keys", evidence())]
    with pytest.raises(OSError):
        observe_and_save(StubVision(labels), jpeg, None, TS)
    assert not memory.get_map()["places"] and not db.recent_sightings("wallet")
    assert not list(db.thumbs_dir().glob("*.jpg"))


def test_api_defaults_to_auto_with_no_room_and_can_opt_out(client, jpeg):
    upload = {"frame": ("frame.jpg", jpeg)}
    result = client.post("/api/observe", files=upload, data={"session_id": "phone_a"})
    assert result.status_code == 200
    assert all(s["place_source"] == "automatic" for s in result.json()["sightings"])
    off = client.post("/api/observe", files=upload, data={"auto_place": "false"})
    assert off.status_code == 200
    assert all(s["place_id"] is None and s["place_source"] == "none" for s in off.json()["sightings"])
    assert client.post("/api/observe", files=upload, data={"session_id": "bad id"}).status_code == 422


def test_explicit_place_bypasses_inference_of_places_and_fills_room(client, jpeg, monkeypatch):
    from app import main
    teach()
    def unexpected(*args):
        pytest.fail("Explicit overrides must use the detection-only request")
    monkeypatch.setattr(main.get_vision(), "observe", unexpected)
    result = client.post("/api/observe", files={"frame": ("frame.jpg", jpeg)}, data={"place_id": "counter"})
    assert result.status_code == 200
    assert all(s["place_source"] == "manual" and s["room"] == "kitchen" for s in result.json()["sightings"])


def test_upgrade_existing_place_links_sets_manual_provenance(tmp_path, monkeypatch):
    path = tmp_path / "previous-memory.db"
    with sqlite3.connect(path) as c:
        c.executescript(db.SCHEMA)
        c.execute("INSERT INTO places VALUES ('counter','counter','kitchen','By the sink','[]')")
        c.execute("ALTER TABLE sightings ADD COLUMN place_id TEXT REFERENCES places(id)")
        c.execute("INSERT INTO sightings VALUES (9,'wallet','kitchen','counter',.1,.2,.3,.4,NULL,?, '', 'counter')", (TS,))
    monkeypatch.setenv("DB_PATH", str(path))
    old = db.recent_sightings("wallet")[0]
    assert old["id"] == 9 and old["place_id"] == "counter" and old["place_source"] == "manual"
    assert old["confidence"] is None
