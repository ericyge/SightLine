"""ElevenLabs text-to-speech relay with mocked HTTP. No real network, no real key."""
import logging

import httpx
import pytest

from app import tts

KEY = "xi-test-SECRET-789"
VOICE = "voice/ID 1"           # odd characters prove the id is URL-quoted
MP3 = b"ID3\x03\x00fake-mp3-bytes"


@pytest.fixture()
def eleven_env(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "elevenlabs")
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", VOICE)
    monkeypatch.delenv("ELEVENLABS_TTS_MODEL", raising=False)
    monkeypatch.delenv("ELEVENLABS_BASE_URL", raising=False)
    tts.CACHE.clear()


def ok(request):
    return httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})


class Calls:
    """A mock transport that counts requests and keeps them."""

    def __init__(self, handler=ok):
        self.requests, self.handler = [], handler
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request):
        self.requests.append(request)
        return self.handler(request)


def use(monkeypatch, calls):
    monkeypatch.setattr(tts, "get_tts", lambda: tts.ElevenLabsTTS(transport=calls.transport))
    return calls


# ---- client -----------------------------------------------------------------------------

def test_request_shape(eleven_env):
    calls = Calls()
    audio = tts.ElevenLabsTTS(transport=calls.transport).speak("Looking for your wallet.")
    assert audio == MP3
    req = calls.requests[0]
    assert req.method == "POST"
    assert req.url.raw_path.startswith(b"/v1/text-to-speech/voice%2FID%201?")   # voice id is URL-quoted
    assert req.url.params["output_format"] == "mp3_44100_128"
    assert req.headers["xi-api-key"] == KEY
    import json
    assert json.loads(req.read()) == {"text": "Looking for your wallet.", "model_id": "eleven_flash_v2_5"}


def test_model_and_base_overrides_and_blank_defaults(eleven_env, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_TTS_MODEL", "eleven_multilingual_v2")
    monkeypatch.setenv("ELEVENLABS_BASE_URL", "https://eu.example/v1/")
    calls = Calls()
    tts.ElevenLabsTTS(transport=calls.transport).speak("Hello there")
    assert str(calls.requests[0].url).startswith("https://eu.example/v1/text-to-speech/")
    assert b"eleven_multilingual_v2" in calls.requests[0].read()
    tts.CACHE.clear()
    monkeypatch.setenv("ELEVENLABS_TTS_MODEL", "")       # blank (as in .env.example) = default
    monkeypatch.setenv("ELEVENLABS_BASE_URL", "")
    tts.ElevenLabsTTS(transport=calls.transport).speak("Hello there")
    assert str(calls.requests[1].url).startswith("https://api.elevenlabs.io/v1/")
    assert b"eleven_flash_v2_5" in calls.requests[1].read()


def test_timeout(eleven_env):
    def slow(request):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(tts.TTSError, match="timeout"):
        tts.ElevenLabsTTS(transport=Calls(slow).transport).speak("Hello there")


@pytest.mark.parametrize("status,expected", [(401, "auth denied"), (403, "auth denied"), (429, "quota or rate limit"),
                                             (402, "quota or rate limit"), (422, "HTTP 422"), (500, "HTTP 500")])
def test_http_errors(eleven_env, status, expected):
    c = Calls(lambda r: httpx.Response(status, text="provider secret detail"))
    with pytest.raises(tts.TTSError) as e:
        tts.ElevenLabsTTS(transport=c.transport).speak("Hello there")
    assert str(e.value) == expected


def test_empty_audio_is_an_error_and_not_cached(eleven_env):
    c = Calls(lambda r: httpx.Response(200, content=b""))
    with pytest.raises(tts.TTSError, match="empty"):
        tts.ElevenLabsTTS(transport=c.transport).speak("Hello there")
    assert len(tts.CACHE) == 0


def test_missing_config_raises_without_a_request(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    c = Calls()
    with pytest.raises(tts.TTSError, match="VOICE_ID"):
        tts.ElevenLabsTTS(transport=c.transport).speak("Hello there")
    assert c.requests == []


def test_failures_are_not_cached(eleven_env):
    bad, good = Calls(lambda r: httpx.Response(500)), Calls()
    with pytest.raises(tts.TTSError):
        tts.ElevenLabsTTS(transport=bad.transport).speak("Hello there")
    assert tts.ElevenLabsTTS(transport=good.transport).speak("Hello there") == MP3
    assert len(good.requests) == 1


# ---- cache ------------------------------------------------------------------------------

def test_short_phrases_are_cached(eleven_env):
    calls = Calls()
    client = tts.ElevenLabsTTS(transport=calls.transport)
    assert client.cached("Reach out now") is None
    client.speak("Reach out now")
    client.speak("Reach out now")
    client.speak("  reach OUT now ")                 # whitespace and case do not matter
    assert len(calls.requests) == 1
    assert client.cached("Reach out now") == MP3


def test_long_text_is_not_cached(eleven_env):
    calls = Calls()
    client = tts.ElevenLabsTTS(transport=calls.transport)
    long_text = "word " * 30
    client.speak(long_text)
    client.speak(long_text)
    assert len(calls.requests) == 2 and len(tts.CACHE) == 0


def test_cache_is_per_voice_and_model(eleven_env, monkeypatch):
    calls = Calls()
    tts.ElevenLabsTTS(transport=calls.transport).speak("Stopped.")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "other-voice")
    tts.ElevenLabsTTS(transport=calls.transport).speak("Stopped.")
    monkeypatch.setenv("ELEVENLABS_TTS_MODEL", "other-model")
    tts.ElevenLabsTTS(transport=calls.transport).speak("Stopped.")
    assert len(calls.requests) == 3


def test_cache_evicts_least_recently_used():
    cache = tts.AudioCache(entries=2)
    cache.put("a", b"1")
    cache.put("b", b"2")
    cache.get("a")                                     # a is now the most recent
    cache.put("c", b"3")
    assert cache.get("a") == b"1" and cache.get("b") is None and cache.get("c") == b"3"


def test_cache_keys_are_hashes_not_text(eleven_env):
    tts.ElevenLabsTTS(transport=Calls().transport).speak("My secret sentence")
    assert all("secret" not in k and len(k) == 64 for k in tts.CACHE._items)


def test_prewarm_fills_the_cache_and_ignores_failures(eleven_env):
    calls = Calls()
    assert tts.prewarm(tts.ElevenLabsTTS(transport=calls.transport)) == len(tts.PREWARM_PHRASES)
    assert len(calls.requests) == len(tts.PREWARM_PHRASES)
    tts.CACHE.clear()
    assert tts.prewarm(tts.ElevenLabsTTS(transport=Calls(lambda r: httpx.Response(401)).transport)) == 0


# ---- settings ---------------------------------------------------------------------------

def test_enabled_only_with_provider_key_and_voice(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "elevenlabs")
    monkeypatch.setenv("ELEVENLABS_API_KEY", KEY)
    assert not tts.tts_enabled() and tts.get_tts() is None            # no voice id
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "your_voice_id_here")
    assert not tts.tts_enabled()                                       # placeholder voice id
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "abc")
    assert tts.tts_enabled() and tts.get_tts().provider == "elevenlabs"
    monkeypatch.setenv("TTS_PROVIDER", "browser")
    assert not tts.tts_enabled()                                       # default stays the built-in voice


def test_guidance_cues_setting(monkeypatch):
    assert tts.guidance_cues_mode() == "always"        # one voice by default
    for value, expected in (("never", "never"), ("NEVER", "never"), ("cached", "cached"), ("Always", "always"),
                            ("junk", "always"), ("", "always")):
        monkeypatch.setenv("TTS_GUIDANCE", value)
        assert tts.guidance_cues_mode() == expected


# ---- endpoint ---------------------------------------------------------------------------

def speak(client, text="Looking for your wallet.", **params):
    return client.post("/api/speak", params=params, json={"text": text})


def test_health_reports_tts(client, eleven_env, monkeypatch):
    h = client.get("/api/health").json()
    assert h["tts"] == "elevenlabs" and h["tts_guidance"] == "always"
    monkeypatch.setenv("TTS_GUIDANCE", "never")
    assert client.get("/api/health").json()["tts_guidance"] == "never"
    monkeypatch.setenv("TTS_GUIDANCE", "cached")
    assert client.get("/api/health").json()["tts_guidance"] == "cached"
    monkeypatch.setenv("TTS_PROVIDER", "browser")
    assert client.get("/api/health").json()["tts"] == "browser"


def test_endpoint_off_by_default_is_503(client):
    assert speak(client).status_code == 503


def test_endpoint_returns_mp3_then_serves_from_cache(client, eleven_env, monkeypatch):
    calls = use(monkeypatch, Calls())
    first = speak(client, "Reach out now")
    assert first.status_code == 200 and first.content == MP3
    assert first.headers["content-type"] == "audio/mpeg" and first.headers["x-tts-cache"] == "miss"
    second = speak(client, "Reach out now")
    assert second.content == MP3 and second.headers["x-tts-cache"] == "hit"
    assert len(calls.requests) == 1


def test_cached_only_never_calls_elevenlabs(client, eleven_env, monkeypatch):
    calls = use(monkeypatch, Calls())
    miss = speak(client, "Reach out now", cached_only="true")
    assert miss.status_code == 204 and miss.content == b"" and calls.requests == []
    speak(client, "Reach out now")                                    # fills the cache
    hit = speak(client, "Reach out now", cached_only="true")
    assert hit.status_code == 200 and hit.content == MP3 and len(calls.requests) == 1


def test_endpoint_validation(client, eleven_env, monkeypatch):
    calls = use(monkeypatch, Calls())
    assert client.post("/api/speak", json={"text": ""}).status_code == 422
    assert client.post("/api/speak", json={"text": "x" * 301}).status_code == 422
    assert client.post("/api/speak", json={}).status_code == 422
    assert speak(client, "x" * 300).status_code == 200
    assert len(calls.requests) == 1


@pytest.mark.parametrize("status", [401, 429, 500])
def test_provider_errors_are_502_without_details(client, eleven_env, monkeypatch, status):
    use(monkeypatch, Calls(lambda r: httpx.Response(status, text="provider secret detail")))
    r = speak(client)
    assert r.status_code == 502 and "secret" not in r.text and KEY not in r.text


def test_timeout_is_502_so_the_app_falls_back(client, eleven_env, monkeypatch):
    def slow(request):
        raise httpx.ReadTimeout("slow")

    use(monkeypatch, Calls(slow))
    assert speak(client).status_code == 502


def test_only_the_reply_text_reaches_elevenlabs(client, eleven_env, monkeypatch):
    calls = use(monkeypatch, Calls())
    speak(client, "Looking for your wallet.")
    req = calls.requests[0]
    assert req.headers["content-type"] == "application/json"
    assert set(req.url.params) == {"output_format"}
    import json
    assert set(json.loads(req.read())) == {"text", "model_id"}          # no audio, frames or history


def test_logs_never_contain_key_or_spoken_text(client, eleven_env, monkeypatch, caplog):
    use(monkeypatch, Calls(lambda r: httpx.Response(401)))
    with caplog.at_level(logging.DEBUG):
        speak(client, "My very private sentence")
    use(monkeypatch, Calls())
    with caplog.at_level(logging.DEBUG):
        speak(client, "My very private sentence")
    for secret in (KEY, "private sentence"):
        assert secret not in caplog.text
