"""Live Perceptron client with the real SDK parsing mocked HTTP replies. No real network or key."""
import io
import json
import logging
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from app import main, vision
from app.vision import PerceptronVision, VisionBusy, VisionStats, box_from_sdk, match_class
from tests.test_api import frame

KEY = "pk-test-SECRET-321"
_buf = io.BytesIO()
Image.new("RGB", (64, 48), (200, 30, 30)).save(_buf, "JPEG")
JPEG = _buf.getvalue()            # the SDK sniffs the format, so test frames must be real images
ITEMS = [{"name": "wallet", "description": "red leather wallet"},
         {"name": "keys", "description": "keys on a bright keychain"}]


def completion(content: str) -> dict:
    """A minimal OpenAI-style chat completion, as the Perceptron API returns."""
    return {"id": "req-1", "model": "perceptron-mk1", "usage": {},
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content}}]}


def tag(mention: str, a: tuple, b: tuple) -> str:
    return f'<point_box mention="{mention}"> ({a[0]},{a[1]}) ({b[0]},{b[1]}) </point_box>'


@pytest.fixture()
def live(monkeypatch):
    """A PerceptronVision whose SDK traffic goes to a mock transport. Set live.handler to script replies."""
    monkeypatch.setenv("PERCEPTRON_API_KEY", KEY)
    for name in ("PERCEPTRON_MODEL", "PERCEPTRON_BASE_URL", "PERCEPTRON_TIMEOUT_S"):
        monkeypatch.delenv(name, raising=False)
    env = SimpleNamespace(seen=[], handler=lambda r: httpx.Response(200, json=completion("")))

    def handle(request):
        env.seen.append(request)
        return env.handler(request)

    monkeypatch.setattr("perceptron.client._http_client",
                        lambda timeout: httpx.Client(transport=httpx.MockTransport(handle), timeout=timeout))
    env.client = PerceptronVision()
    return env


# ---- detection ---------------------------------------------------------------------------

def test_box_found_is_converted_from_the_0_1000_grid(live):
    live.handler = lambda r: httpx.Response(200, json=completion(
        tag("red leather wallet", (300, 500), (400, 700))))
    [d] = live.client.detect(JPEG, ITEMS)
    assert d.item == "wallet" and d.box == [0.3, 0.5, 0.4, 0.7]
    assert (d.box[0] + d.box[2]) / 2 == pytest.approx(0.35)     # center x
    assert d.box[3] - d.box[1] == pytest.approx(0.2)            # height, as guidance.py expects
    assert 0 < d.confidence <= 1


def test_several_items_in_one_call(live):
    live.handler = lambda r: httpx.Response(200, json=completion(
        tag("red leather wallet", (100, 100), (200, 200)) +
        tag("keys on a bright keychain", (600, 400), (700, 600))))
    found = {d.item: d.box for d in live.client.detect(JPEG, ITEMS)}
    assert found == {"wallet": [0.1, 0.1, 0.2, 0.2], "keys": [0.6, 0.4, 0.7, 0.6]}
    assert len(live.seen) == 1                                   # one request, not one per item


def test_nothing_found(live):
    live.handler = lambda r: httpx.Response(200, json=completion("I can't see that."))
    assert live.client.detect(JPEG, ITEMS) == []
    assert live.client.stats()["calls"] == 1 and live.client.stats()["errors"] == 0


def test_request_shape_and_privacy(live, caplog):
    live.handler = lambda r: httpx.Response(200, json=completion(tag("red leather wallet", (1, 1), (9, 9))))
    with caplog.at_level(logging.DEBUG):
        live.client.detect(JPEG, ITEMS)
    req = live.seen[0]
    body = json.loads(req.content)
    assert str(req.url) == "https://api.perceptron.inc/v1/chat/completions"
    assert req.headers["authorization"] == f"Bearer {KEY}"
    assert body["model"] == "perceptron-mk1"
    text = json.dumps(body)
    assert "red leather wallet" in text and "keys on a bright keychain" in text   # classes = descriptions
    assert KEY not in text
    assert KEY not in caplog.text and "/9j/" not in caplog.text   # no key, no base64 frame


def test_model_comes_from_env(live, monkeypatch):
    monkeypatch.setenv("PERCEPTRON_MODEL", "some-other-id")
    client = PerceptronVision()
    client.detect(JPEG, ITEMS)
    assert json.loads(live.seen[-1].content)["model"] == "some-other-id"


def test_description_falls_back_to_name(live):
    live.client.detect(JPEG, [{"name": "wallet", "description": ""}])
    assert '"wallet"' in live.seen[0].content.decode() or "wallet" in live.seen[0].content.decode()


def test_no_items_makes_no_call(live):
    assert live.client.detect(JPEG, []) == [] and live.seen == []


# ---- errors never crash a frame -----------------------------------------------------------

def timeout(request):
    raise httpx.ReadTimeout("slow")


@pytest.mark.parametrize("handler", [
    timeout,
    lambda r: httpx.Response(429, json={"error": {"message": "slow down", "code": "rate_limit"}}),
    lambda r: httpx.Response(401, json={"error": {"message": "bad key"}}),
    lambda r: httpx.Response(500, text="boom"),
    lambda r: httpx.Response(200, text="not json at all"),
    lambda r: httpx.Response(200, json={"unexpected": True}),
], ids=["timeout", "429", "401", "500", "not-json", "bad-shape"])
def test_failures_mean_not_seen(live, handler):
    live.handler = handler
    assert live.client.detect(JPEG, ITEMS) == []
    s = live.client.stats()
    assert s["errors"] == 1 and s["calls"] == 0


@pytest.mark.parametrize("markup", ['<point_box mention="x"> (30 ', '<point_box mention="x"> (1,2) </point_box>',
                                    '<point_box mention="x"> (abc,def) (5,6) </point_box>'])
def test_malformed_markup_means_not_seen(live, markup):
    """The SDK tolerates broken markup (no boxes, no exception); either way the frame is 'not seen'."""
    live.handler = lambda r: httpx.Response(200, json=completion(markup))
    assert live.client.detect(JPEG, ITEMS) == []


def test_client_recovers_after_a_failure(live):
    live.handler = timeout
    assert live.client.detect(JPEG, ITEMS) == []
    live.handler = lambda r: httpx.Response(200, json=completion(tag("red leather wallet", (300, 500), (400, 700))))
    assert live.client.detect(JPEG, ITEMS)[0].item == "wallet"


def test_repeated_failures_warn_once_per_minute(live, caplog, monkeypatch):
    live.handler = timeout
    clock = {"t": 1000.0}
    monkeypatch.setattr(vision.time, "monotonic", lambda: clock["t"])
    with caplog.at_level(logging.WARNING, logger="sightline.vision"):
        for _ in range(5):
            live.client.detect(JPEG, ITEMS)
        assert sum("Perceptron call failed" in r.message for r in caplog.records) == 1
        clock["t"] += 61
        live.client.detect(JPEG, ITEMS)
        assert sum("Perceptron call failed" in r.message for r in caplog.records) == 2
    assert KEY not in caplog.text


# ---- one call in flight -------------------------------------------------------------------

def test_second_frame_is_dropped_while_one_is_running(live):
    entered, release = threading.Event(), threading.Event()

    def slow(request):
        entered.set()
        release.wait(5)
        return httpx.Response(200, json=completion(tag("red leather wallet", (300, 500), (400, 700))))

    live.handler = slow
    first = []
    worker = threading.Thread(target=lambda: first.extend(live.client.detect(JPEG, ITEMS)))
    worker.start()
    assert entered.wait(5)
    with pytest.raises(VisionBusy):
        live.client.detect(JPEG, ITEMS)                       # dropped, not queued
    assert live.client.stats()["dropped"] == 1
    release.set()
    worker.join(5)
    assert first and first[0].item == "wallet"
    assert len(live.seen) == 1                                    # the dropped frame never reached the API
    assert live.client.detect(JPEG, ITEMS)                     # free again afterwards


def test_endpoints_answer_204_when_busy_and_recover(client, live, monkeypatch, jpeg):
    entered, release = threading.Event(), threading.Event()

    def slow(request):
        entered.set()
        release.wait(5)
        return httpx.Response(200, json=completion(tag("red leather wallet", (300, 500), (400, 700))))

    live.handler = slow
    monkeypatch.setattr(main, "get_vision", lambda: live.client)
    results = {}
    worker = threading.Thread(target=lambda: results.update(
        first=client.post("/api/guide", files=frame(jpeg), data={"item": "wallet"})))
    worker.start()
    assert entered.wait(5)
    assert client.post("/api/guide", files=frame(jpeg), data={"item": "wallet"}).status_code == 204
    obs = client.post("/api/observe", files=frame(jpeg), data={"room": "bedroom"})
    assert obs.status_code == 204 and obs.content == b""
    release.set()
    worker.join(5)
    assert results["first"].status_code == 200 and results["first"].json()["found"] is True
    assert client.post("/api/guide", files=frame(jpeg), data={"item": "wallet"}).status_code == 200


def test_guide_endpoint_treats_a_failed_call_as_lost(client, live, monkeypatch, jpeg):
    live.handler = timeout
    monkeypatch.setattr(main, "get_vision", lambda: live.client)
    r = client.post("/api/guide", files=frame(jpeg), data={"item": "wallet"})
    assert r.status_code == 200 and r.json()["found"] is False and r.json()["beep_ms"] == 0


def test_guide_endpoint_end_to_end(client, live, monkeypatch, jpeg):
    live.handler = lambda r: httpx.Response(200, json=completion(tag("a wallet", (700, 550), (770, 690))))
    monkeypatch.setattr(main, "get_vision", lambda: live.client)
    r = client.post("/api/guide", files=frame(jpeg), data={"item": "wallet"}).json()
    assert r["found"] and r["box"] == [0.7, 0.55, 0.77, 0.69]
    assert r["clock"] == 1 and r["distance"] == "near" and r["pan"] > 0   # center x 0.735 = 15 deg right


# ---- coordinate conversion ---------------------------------------------------------------

def sdk_box(x1, y1, x2, y2, mention=None):
    pt = lambda x, y: SimpleNamespace(x=x, y=y)  # noqa: E731
    return SimpleNamespace(top_left=pt(x1, y1), bottom_right=pt(x2, y2), mention=mention)


@pytest.mark.parametrize("raw,expected", [
    ((0, 0, 1000, 1000), [0.0, 0.0, 1.0, 1.0]),
    ((250, 500, 500, 750), [0.25, 0.5, 0.5, 0.75]),
    ((500, 750, 250, 500), [0.25, 0.5, 0.5, 0.75]),          # corners swapped
    ((-20, -5, 1200, 1100), [0.0, 0.0, 1.0, 1.0]),           # out of range is clamped
    ((1, 2, 3, 4), [0.001, 0.002, 0.003, 0.004]),
])
def test_box_conversion(raw, expected):
    assert box_from_sdk(sdk_box(*raw)) == expected


def test_degenerate_boxes_are_dropped():
    assert box_from_sdk(sdk_box(100, 100, 100, 300)) is None
    assert box_from_sdk(sdk_box(100, 300, 400, 300)) is None


def test_match_class():
    classes = ["red leather wallet", "keys on a bright keychain"]
    assert match_class("Red Leather Wallet", classes) == 0
    assert match_class("keys", classes) == 1                   # contained in the class text
    assert match_class("a red leather wallet on a desk", classes) == 0
    assert match_class("banana", classes) is None
    assert match_class(None, classes) is None
    assert match_class("anything", ["only one"]) == 0          # a single class takes any box
    assert match_class(None, ["only one"]) == 0


# ---- latency stats and health -------------------------------------------------------------

def test_stats_percentiles():
    s = VisionStats()
    assert s.snapshot() == {"calls": 0, "p50_ms": None, "p95_ms": None, "errors": 0, "dropped": 0}
    for ms in range(100, 2100, 100):                            # 100..2000 ms, 20 calls
        s.record(ms / 1000)
    s.error()
    s.drop()
    s.drop()
    snap = s.snapshot()
    assert snap == {"calls": 20, "p50_ms": 1000, "p95_ms": 1900, "errors": 1, "dropped": 2}


def test_stats_window_keeps_recent_calls_only():
    s = VisionStats(window=3)
    for sec in (9, 9, 9, 1, 1, 1):
        s.record(sec)
    assert s.snapshot()["p50_ms"] == 1000


def test_health_exposes_latency_and_fps_cap(client, live, monkeypatch):
    live.handler = lambda r: httpx.Response(200, json=completion(""))
    monkeypatch.setattr(main, "get_vision", lambda: live.client)
    live.client.detect(JPEG, ITEMS)
    h = client.get("/api/health").json()
    assert h["mode"] == "live" and h["mock"] is False
    assert h["vision"]["calls"] == 1 and h["vision"]["p50_ms"] is not None
    assert h["guide_max_fps"] == 3.0


@pytest.mark.parametrize("value,expected", [("2", 2.0), ("", 3.0), ("abc", 3.0), ("0", 0.5), ("99", 10.0)])
def test_guide_max_fps_is_configurable(client, monkeypatch, value, expected):
    monkeypatch.setenv("GUIDE_MAX_FPS", value)
    assert client.get("/api/health").json()["guide_max_fps"] == expected


# ---- selecting the mode -------------------------------------------------------------------

def test_live_mode_needs_a_key(monkeypatch):
    monkeypatch.setenv("PERCEPTRON_MODE", "live")
    monkeypatch.delenv("PERCEPTRON_API_KEY", raising=False)
    vision.reset_vision()
    with pytest.raises(RuntimeError, match="PERCEPTRON_API_KEY"):
        vision.get_vision()
    vision.reset_vision()


def test_modes_select_the_right_client(monkeypatch):
    monkeypatch.setenv("PERCEPTRON_API_KEY", KEY)
    for mode, cls in (("mock", vision.MockVision), ("", vision.MockVision),
                      ("live", PerceptronVision), ("perceptron", PerceptronVision)):
        monkeypatch.setenv("PERCEPTRON_MODE", mode)
        vision.reset_vision()
        assert isinstance(vision.get_vision(), cls)
    monkeypatch.delenv("PERCEPTRON_MODE")
    vision.reset_vision()
    assert vision.get_vision().mock is True                     # default stays mock
    vision.reset_vision()


# ---- probe script ------------------------------------------------------------------------

def run_probe(monkeypatch, capsys, *args):
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "scripts" / "perceptron_probe.py"
    spec = importlib.util.spec_from_file_location("perceptron_probe", path)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    monkeypatch.setattr("sys.argv", ["perceptron_probe.py", *args])
    code = probe.main()
    return code, capsys.readouterr().out


def test_probe_prints_raw_and_converted_box(live, monkeypatch, capsys, tmp_path):
    img = tmp_path / "f.jpg"
    img.write_bytes(JPEG)
    live.handler = lambda r: httpx.Response(200, json=completion(tag("red leather wallet", (700, 550), (770, 690))))
    code, out = run_probe(monkeypatch, capsys, str(img), "red leather wallet")
    assert code == 0
    assert "raw (0-1000 grid): x1=700 y1=550 x2=770 y2=690" in out
    assert "converted (0..1): [0.7, 0.55, 0.77, 0.69]" in out
    assert "center x=0.735" in out and "height=0.140" in out and "clock=1" in out
    assert " ms" in out and KEY not in out


def test_probe_repeat_prints_percentiles_and_survives_failures(live, monkeypatch, capsys, tmp_path):
    img = tmp_path / "f.jpg"
    img.write_bytes(JPEG)
    calls = {"n": 0}

    def flaky(request):
        calls["n"] += 1
        if calls["n"] == 2:
            raise httpx.ReadTimeout("slow")
        return httpx.Response(200, json=completion(""))

    live.handler = flaky
    code, out = run_probe(monkeypatch, capsys, str(img), "wallet", "--repeat", "4")
    assert code == 0 and "FAILED (TimeoutError)" in out
    assert "p50=" in out and "p95=" in out and "1 of 4 calls failed" in out


def test_probe_exit_codes(live, monkeypatch, capsys, tmp_path):
    img = tmp_path / "f.jpg"
    img.write_bytes(JPEG)
    assert run_probe(monkeypatch, capsys, str(tmp_path / "missing.jpg"), "x")[0] == 2
    live.handler = lambda r: httpx.Response(401, json={"error": {"message": "bad key"}})
    code, out = run_probe(monkeypatch, capsys, str(img), "wallet")
    assert code == 1 and "FAILED" in out
    monkeypatch.delenv("PERCEPTRON_API_KEY")
    code, out = run_probe(monkeypatch, capsys, str(img), "wallet")
    assert code == 1 and "PERCEPTRON_API_KEY" in out
