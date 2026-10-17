import io
import os

import pytest
from PIL import Image

os.environ["PERCEPTRON_MODE"] = "mock"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient in mock mode with a fresh temp DB and thumbs dir."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("THUMBS_DIR", str(tmp_path / "thumbs"))
    monkeypatch.setenv("PERCEPTRON_MODE", "mock")
    from fastapi.testclient import TestClient
    from app import main, vision
    vision.reset_vision()
    main._guide_states.clear()
    (tmp_path / "thumbs").mkdir()
    with TestClient(main.app) as c:   # `with` keeps one event loop so WebSocket events flow
        yield c


@pytest.fixture()
def jpeg() -> bytes:
    """A 640x480 JPEG standing in for a phone frame."""
    buf = io.BytesIO()
    Image.new("RGB", (640, 480), (90, 120, 160)).save(buf, "JPEG")
    return buf.getvalue()
