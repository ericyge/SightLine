import io
import os

import pytest
from PIL import Image

os.environ["PERCEPTRON_MODE"] = "mock"
os.environ["LLM_PROVIDER"] = "mock"   # a teammate's .env must never make tests hit the network


PROVIDER_VARS = ["PURPLE_API_BASE", "PURPLE_API_TOKEN", "PURPLE_MODEL", "LLM_BASE_URL", "LLM_API_KEY",
                 "LLM_MODEL", "GEMINI_API_KEY", "GEMINI_BASE_URL", "GEMINI_MODEL", "LLM_TIMEOUT_S", "ELEVENLABS_API_KEY",
                 "ELEVENLABS_STT_MODEL", "ELEVENLABS_BASE_URL", "STT_TIMEOUT_S", "PERCEPTRON_API_KEY",
                 "PERCEPTRON_MODEL", "PERCEPTRON_BASE_URL", "PERCEPTRON_TIMEOUT_S", "GUIDE_MAX_FPS",
                 "ELEVENLABS_VOICE_ID", "ELEVENLABS_TTS_MODEL", "TTS_TIMEOUT_S", "TTS_GUIDANCE",
                 "WAKE_ENABLED", "WAKE_THRESHOLD", "WAKE_MODEL_DIR"]


@pytest.fixture(autouse=True)
def _no_provider_env(monkeypatch):
    """A developer's real .env (loaded when app.main is imported) must never reach tests."""
    for name in PROVIDER_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("STT_PROVIDER", "browser")
    monkeypatch.setenv("TTS_PROVIDER", "browser")
    from app import tts
    tts.CACHE.clear()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient in mock mode with a fresh temp DB and thumbs dir."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("THUMBS_DIR", str(tmp_path / "thumbs"))
    monkeypatch.setenv("PERCEPTRON_MODE", "mock")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    from fastapi.testclient import TestClient
    from app import main, vision
    vision.reset_vision()
    main._guide_states.clear()
    main._navigate_states.clear()
    (tmp_path / "thumbs").mkdir()
    with TestClient(main.app) as c:   # `with` keeps one event loop so WebSocket events flow
        yield c


@pytest.fixture()
def jpeg() -> bytes:
    """A 640x480 JPEG standing in for a phone frame."""
    buf = io.BytesIO()
    Image.new("RGB", (640, 480), (90, 120, 160)).save(buf, "JPEG")
    return buf.getvalue()
