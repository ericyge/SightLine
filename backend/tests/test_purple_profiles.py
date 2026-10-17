"""Keep purple/ sample profiles valid and the validator honest."""
import importlib.util
import json
from pathlib import Path

import pytest

PURPLE = Path(__file__).resolve().parents[2] / "purple"
spec = importlib.util.spec_from_file_location("validate_profile", PURPLE / "validate_profile.py")
vp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vp)

SAMPLES = [PURPLE / "sample-profile.json", *sorted((PURPLE / "samples").glob("*.json"))]


@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.name)
def test_samples_have_no_errors(path):
    errors, _ = vp.check(json.loads(path.read_text(encoding="utf-8")))
    assert errors == []


def test_extract_json_from_agent_reply():
    reply = 'Got it.\n```json\n{"watchlist": [], "rooms": [], "guidance_style": "clock_face"}\n```'
    assert vp.extract_json(reply)["guidance_style"] == "clock_face"


def test_extract_json_without_fence():
    assert vp.extract_json('ok {"a": 1} done') == {"a": 1}


def test_extract_json_missing():
    with pytest.raises(ValueError):
        vp.extract_json("no json here")


def test_bad_style_is_error():
    errors, _ = vp.check({"watchlist": [{"name": "x"}], "rooms": ["a"], "guidance_style": "loud"})
    assert errors


def test_empty_and_duplicate_are_errors():
    errors, _ = vp.check({"watchlist": [], "rooms": [], "guidance_style": "clock_face"})
    assert "watchlist is empty" in errors and "rooms is empty" in errors
    dup = {"watchlist": [{"name": "a"}, {"name": "A"}], "rooms": ["r"], "guidance_style": "clock_face"}
    assert any("duplicate" in e for e in vp.check(dup)[0])


def test_thin_description_warns_not_errors():
    errors, warnings = vp.check({"watchlist": [{"name": "wallet", "description": "wallet",
                                                "home_spot": "bedroom desk"}],
                                 "rooms": ["bedroom"], "guidance_style": "clock_face"})
    assert errors == [] and any("description" in w for w in warnings)
