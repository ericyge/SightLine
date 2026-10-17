"""Speech-to-text relay with a mocked ElevenLabs. No real network, no real key."""
import logging

import httpx
import pytest

from app import stt

KEY = "xi-test-SECRET-789"
WAV = b"RIFF....WAVEfmt fake-audio-bytes"


@pytest.fixture()
def eleven_env(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "elevenlabs")
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    monkeypatch.delenv("ELEVENLABS_STT_MODEL", raising=False)
    monkeypatch.delenv("ELEVENLABS_BASE_URL", raising=False)


def client_with(monkeypatch, handler):
    """Make get_stt() return an ElevenLabsSTT on a mock transport; returns the request log."""
    seen = []

    def wrapped(request):
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(stt, "get_stt", lambda: stt.ElevenLabsSTT(transport=httpx.MockTransport(wrapped)))
    return seen


def ok(request):
    return httpx.Response(200, json={"text": "  where is my wallet  ", "language_code": "eng"})


# ---- client ----------------------------------------------------------------------------

def test_request_shape(eleven_env):
    seen = []

    def handler(request):
        seen.append(request)
        return ok(request)

    c = stt.ElevenLabsSTT(transport=httpx.MockTransport(handler))
    assert c.transcribe(WAV, "audio/wav") == "where is my wallet"
    req = seen[0]
    assert str(req.url) == "https://api.elevenlabs.io/v1/speech-to-text"
    assert req.headers["xi-api-key"] == KEY
    body = req.content
    assert b'name="model_id"' in body and b"scribe_v2" in body
    assert b'name="file"; filename="recording.wav"' in body and WAV in body
    assert b'name="diarize"' in body and b'name="tag_audio_events"' in body


def test_model_and_base_overrides_and_blank_defaults(eleven_env, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_STT_MODEL", "scribe_next")
    monkeypatch.setenv("ELEVENLABS_BASE_URL", "https://eu.example/v1/")
    seen = []
    stt.ElevenLabsSTT(transport=httpx.MockTransport(lambda r: seen.append(r) or ok(r))).transcribe(WAV)
    assert str(seen[0].url) == "https://eu.example/v1/speech-to-text" and b"scribe_next" in seen[0].content
    monkeypatch.setenv("ELEVENLABS_STT_MODEL", "")      # blank (as in .env.example) = default
    monkeypatch.setenv("ELEVENLABS_BASE_URL", "")
    stt.ElevenLabsSTT(transport=httpx.MockTransport(lambda r: seen.append(r) or ok(r))).transcribe(WAV)
    assert str(seen[1].url).startswith("https://api.elevenlabs.io/v1") and b"scribe_v2" in seen[1].content


@pytest.mark.parametrize("status,expected", [(401, "auth denied"), (403, "auth denied"),
                                             (402, "quota or rate limit"), (429, "quota or rate limit"),
                                             (500, "HTTP 500")])
def test_http_errors(eleven_env, status, expected):
    c = stt.ElevenLabsSTT(transport=httpx.MockTransport(lambda r: httpx.Response(status, text="secret body")))
    with pytest.raises(stt.STTError) as e:
        c.transcribe(WAV)
    assert str(e.value) == expected


def test_timeout_and_bad_responses(eleven_env):
    def timeout(request):
        raise httpx.ReadTimeout("slow")

    for handler, message in ((timeout, "timeout"),
                             (lambda r: httpx.Response(200, text="not json"), "bad response"),
                             (lambda r: httpx.Response(200, json={"nope": 1}), "bad response"),
                             (lambda r: httpx.Response(200, json={"text": 5}), "bad response")):
        with pytest.raises(stt.STTError, match=message):
            stt.ElevenLabsSTT(transport=httpx.MockTransport(handler)).transcribe(WAV)


def test_empty_transcript_is_fine(eleven_env):
    c = stt.ElevenLabsSTT(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"text": ""})))
    assert c.transcribe(WAV) == ""


def test_get_stt_needs_provider_and_key(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "browser")
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    assert stt.get_stt() is None                              # provider not selected
    monkeypatch.setenv("STT_PROVIDER", "elevenlabs")
    assert stt.get_stt().provider == "elevenlabs"
    for blank in ("", "your_api_key_here"):
        monkeypatch.setenv("ELEVENLABS_API_KEY", blank)
        assert stt.get_stt() is None                          # missing key -> browser fallback


# ---- endpoint --------------------------------------------------------------------------

def upload(client, data=WAV, content_type="audio/wav"):
    return client.post("/api/transcribe", files={"audio": ("rec.wav", data, content_type)})


def test_health_reports_stt_mode(client, eleven_env, monkeypatch):
    assert client.get("/api/health").json()["stt"] == "elevenlabs"
    monkeypatch.setenv("STT_PROVIDER", "browser")
    assert client.get("/api/health").json()["stt"] == "browser"


def test_endpoint_not_configured_is_503(client):
    assert upload(client).status_code == 503


def test_endpoint_transcribes(client, eleven_env, monkeypatch):
    seen = client_with(monkeypatch, ok)
    r = upload(client)
    assert r.status_code == 200 and r.json() == {"text": "where is my wallet", "provider": "elevenlabs"}
    assert len(seen) == 1


def test_endpoint_validation(client, eleven_env, monkeypatch):
    seen = client_with(monkeypatch, ok)
    assert upload(client, b"").status_code == 400
    assert upload(client, b"x" * 5_000_001).status_code == 413
    assert client.post("/api/transcribe", data={"audio": "text"}).status_code == 422
    assert seen == []                                         # nothing reached the provider


@pytest.mark.parametrize("status", [401, 429, 500])
def test_endpoint_provider_errors_are_502_without_details(client, eleven_env, monkeypatch, status):
    client_with(monkeypatch, lambda r: httpx.Response(status, text="provider secret detail"))
    r = upload(client)
    assert r.status_code == 502 and "secret" not in r.text and KEY not in r.text


def test_endpoint_logs_never_contain_key_audio_or_transcript(client, eleven_env, monkeypatch, caplog):
    client_with(monkeypatch, lambda r: httpx.Response(500, text="x"))
    with caplog.at_level(logging.DEBUG):
        upload(client, b"private-audio-bytes")
    client_with(monkeypatch, ok)
    with caplog.at_level(logging.DEBUG):
        upload(client)
    for secret in (KEY, "private-audio-bytes", "where is my wallet"):
        assert secret not in caplog.text


def test_audio_not_stored_anywhere(client, eleven_env, monkeypatch, tmp_path):
    client_with(monkeypatch, ok)
    before = {p for p in tmp_path.rglob("*") if p.is_file()}
    upload(client)
    assert {p for p in tmp_path.rglob("*") if p.is_file()} == before
