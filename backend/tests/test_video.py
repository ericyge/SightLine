"""Use a tiny generated clip to verify timestamps, sampling, and saved memory."""
import io
import json
import sqlite3

import av
import pytest
from PIL import Image

from app import db
from app.vision.cli import main
from app.vision.video import sample_video


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / "synthetic.mp4"
    with av.open(str(path), "w") as output:
        stream = output.add_stream("mpeg4", rate=10)
        stream.width, stream.height = 400, 200
        stream.pix_fmt = "yuv420p"
        for index in range(40):
            image = Image.new("RGB", (400, 200), (index * 5, 80, 120))
            frame = av.VideoFrame.from_image(image)
            frame.pts = index
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    return path


def test_samples_by_time_and_resizes(clip):
    frames = list(sample_video(clip, fps=1, max_px=128))
    assert [offset for offset, _ in frames] == [0, 1, 2, 3]
    assert Image.open(io.BytesIO(frames[0][1])).size == (128, 64)
    assert len(list(sample_video(clip, fps=2, limit=3))) == 3


@pytest.mark.parametrize("fps", [0, -1, float("nan"), 31])
def test_invalid_sampling_rejected(clip, fps):
    with pytest.raises(ValueError):
        list(sample_video(clip, fps=fps))


def test_recorded_video_reaches_where_with_original_times(client, clip, capsys):
    assert main(["video", str(clip), "--mock", "--room", "bedroom",
                 "--started-at", "2026-10-17T12:00:00-07:00"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(rows) == 4 and all(row["mock"] for row in rows)
    assert rows[-1]["ts"] == "2026-10-17T19:00:03+00:00"
    result = client.get("/api/where?item=wallet").json()
    assert result["found"]
    assert result["history"][0]["ts"] == rows[-1]["ts"]
    assert len(result["history"]) == 4
    # Decoding created no extracted frame files, just the permitted thumbnails.
    assert not list(clip.parent.glob("*.jpg"))


def test_video_requires_timezone(clip):
    with pytest.raises(SystemExit):
        main(["video", str(clip), "--room", "bedroom", "--started-at", "2026-10-17T12:00:00"])


def test_benchmark_is_labeled_mock_and_does_not_save_sightings(client, clip, capsys):
    assert main(["benchmark", str(clip), "--mock", "--count", "3",
                 "--models", "candidate-a", "candidate-b", "--task", "guide"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(rows) == 2
    assert all(row["mock"] and row["model"] == "mock" for row in rows)
    assert all(row["successes"] == 3 and row["errors"] == 0 for row in rows)
    assert client.get("/api/where?item=wallet").json()["found"] is False


def test_benchmark_refuses_insufficient_frames(client, clip, capsys):
    assert main(["benchmark", str(clip), "--mock", "--count", "20"]) == 1
    captured = capsys.readouterr()
    assert "provided 4" in captured.err and not captured.out


def test_existing_database_migration_preserves_sightings(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    old_schema = db.SCHEMA.replace("confidence REAL,", "confidence REAL NOT NULL,")
    with sqlite3.connect(path) as connection:
        connection.executescript(old_schema)
        connection.execute("INSERT INTO sightings VALUES (7,'wallet','bedroom','on desk',"
                           "0.1,0.2,0.3,0.4,0.9,'2026-10-17T19:00:00+00:00','')")
    monkeypatch.setenv("DB_PATH", str(path))
    old = db.recent_sightings("wallet")[0]
    assert old["confidence"] == .9 and old["spot"] == "on desk"
    db.add_sighting("wallet", "kitchen", "on counter", [.1, .2, .3, .4], None,
                    "2026-10-17T19:01:00+00:00", "")
    assert db.recent_sightings("wallet")[0]["confidence"] is None
    with db.connect() as connection:
        assert connection.execute("SELECT id FROM sightings ORDER BY id").fetchall()[0]["id"] == 7
        assert connection.execute("SELECT id FROM sightings ORDER BY id DESC").fetchone()["id"] == 8
