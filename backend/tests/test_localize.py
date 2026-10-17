"""Camera place localization: mock path and structured-result validation."""
import json
from pathlib import Path

from app import memory
from app.memory.models import MemoryMapUpdate
from app.vision.vision import LocalizationResult, MockVision, _Localization, _accept_localization

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "two_room_memory.json"


def places():
    return json.loads(EXAMPLE.read_text())["places"]


def test_mock_localize_forced_place(jpeg):
    vision = MockVision()
    result = vision.localize(jpeg, places(), {"place": "kitchen_entry"})
    assert result == LocalizationResult("kitchen_entry", "Mock: forced place match.", [0, 1])


def test_mock_localize_unknown_and_missing(jpeg):
    vision = MockVision()
    assert vision.localize(jpeg, places()).place_id is None
    assert vision.localize(jpeg, places(), {"place": "unknown"}).place_id is None
    assert vision.localize(jpeg, places(), {"place": "missing"}).place_id is None


def test_accept_localization_requires_two_landmarks():
    cands = places()
    weak = _Localization(place_id="kitchen_counter", matched_landmarks=[0], reason="only one")
    assert _accept_localization(weak, cands).place_id is None
    ok = _Localization(place_id="kitchen_counter", matched_landmarks=[0, 2], reason="sink and edge")
    assert _accept_localization(ok, cands).place_id == "kitchen_counter"
    bad_id = _Localization(place_id="nope", matched_landmarks=[0, 1], reason="invented")
    assert _accept_localization(bad_id, cands).place_id is None


def test_vision_cli_localize_mock(tmp_path, monkeypatch, jpeg):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "loc.db"))
    monkeypatch.setenv("THUMBS_DIR", str(tmp_path / "thumbs"))
    (tmp_path / "thumbs").mkdir()
    from app import db
    db._initialized.discard(db.db_path())
    memory.teach_map(MemoryMapUpdate.model_validate(json.loads(EXAMPLE.read_text())))
    image = tmp_path / "frame.jpg"
    image.write_bytes(jpeg)
    from app.vision.cli import main
    assert main(["localize", str(image), "--mock", "--mock-place", "bedroom_door"]) == 0
