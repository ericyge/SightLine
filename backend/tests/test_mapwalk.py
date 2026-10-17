"""Walkthrough mapping builds places/connections from frames without hand-written JSON."""
import av
import pytest
from PIL import Image

from app import memory
from app.memory.mapwalk import apply_survey, mapwalk_video
from app.memory.route import plan_route
from app.vision import MockVision, PlaceSurveyResult
from app.vision.cli import main as vision_cli


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / "walk.mp4"
    with av.open(str(path), "w") as output:
        stream = output.add_stream("mpeg4", rate=10)
        stream.width, stream.height = 320, 240
        stream.pix_fmt = "yuv420p"
        for index in range(60):
            image = Image.new("RGB", (320, 240), (index * 3, 40, 80))
            frame = av.VideoFrame.from_image(image)
            frame.pts = index
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    return path


def test_apply_survey_creates_place_and_connection(client):
    first = apply_survey(PlaceSurveyResult(
        decision="new", name="doorway", room="bedroom",
        description="Bedroom doorway.", landmarks=["door frame", "hallway beyond"],
        reason="test"))
    assert first.action == "new" and first.place_id.startswith("auto_")
    second = apply_survey(PlaceSurveyResult(
        decision="new", name="kitchen counter", room="kitchen",
        description="Counter by the sink.", landmarks=["sink", "cabinets"],
        instruction_from_previous="Walk to the kitchen counter.", reason="test"),
        previous_place_id=first.place_id)
    assert second.connection_added
    saved = memory.get_map()
    assert len(saved["places"]) == 2 and len(saved["connections"]) == 1
    assert saved["connections"][0]["instruction"] == "Walk to the kitchen counter."


def test_mapwalk_mock_video_builds_routable_graph(client, clip, capsys):
    assert vision_cli(["mapwalk", str(clip), "--mock", "--fps", "1", "--limit", "6"]) == 0
    out = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    summary = __import__("json").loads(out[-1])
    assert summary["places"] == 3 and summary["connections"] == 2
    places = summary["map"]["places"]
    start = next(p["id"] for p in places if "doorway" in p["name"])
    end = next(p["id"] for p in places if "counter" in p["name"])
    plan = plan_route(start, end)
    assert plan.status == "ok" and len(plan.hops) == 2
    assert all(h.instruction for h in plan.hops)


def test_mapwalk_rematch_does_not_duplicate_places(client, jpeg):
    vision = MockVision()
    frames = [(0, jpeg), (1, jpeg), (2, jpeg), (3, jpeg)]
    rows = list(mapwalk_video(vision, frames))
    assert rows[0]["action"] == "new"
    assert rows[1]["action"] == "match"
    assert memory.get_map()["places"]  # at least one
    # First two frames are the same mock place phase → one place, no self-edge
    assert rows[1]["connection_added"] is False
