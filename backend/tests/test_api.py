from datetime import datetime, timedelta, timezone


def frame(jpeg):
    return {"frame": ("frame.jpg", jpeg, "image/jpeg")}


def test_health_reports_mock(client):
    assert client.get("/api/health").json() == {"ok": True, "mode": "mock", "mock": True, "stt": "browser", "vision": None, "guide_max_fps": 3.0,
                                                 "tts": "browser", "tts_guidance": "cached", "wake": False}


def test_profile_default_and_roundtrip(client):
    p = client.get("/api/profile").json()
    assert [w["name"] for w in p["watchlist"]] == ["keys", "wallet", "phone", "glasses", "medication"]
    assert p["rooms"] == ["bedroom", "kitchen", "living room"]
    assert p["guidance_style"] == "clock_face"
    new = {"watchlist": [{"name": "wallet", "description": "red wallet", "home_spot": "desk"}],
           "rooms": ["bedroom"], "guidance_style": "left_right"}
    assert client.post("/api/profile", json=new).json() == {"ok": True}
    assert client.get("/api/profile").json() == new


def test_profile_rejects_bad_style(client):
    bad = {"watchlist": [], "rooms": [], "guidance_style": "shouting"}
    assert client.post("/api/profile", json=bad).status_code == 422


def test_observe_saves_sightings_and_thumbs(client, jpeg):
    r = client.post("/api/observe", files=frame(jpeg), data={"room": "bedroom"})
    assert r.status_code == 200
    s = r.json()["sightings"]
    assert {x["item"] for x in s} == {"keys", "wallet", "phone", "glasses", "medication"}
    wallet = next(x for x in s if x["item"] == "wallet")
    assert wallet["room"] == "bedroom" and len(wallet["box"]) == 4
    assert 0 <= min(wallet["box"]) and max(wallet["box"]) <= 1
    assert wallet["thumb"].startswith("/api/thumbs/")
    assert client.get(wallet["thumb"]).status_code == 200


def test_observe_respects_profile_watchlist(client, jpeg):
    client.post("/api/profile", json={"watchlist": [{"name": "wallet"}], "rooms": ["bedroom"],
                                      "guidance_style": "clock_face"})
    s = client.post("/api/observe", files=frame(jpeg), data={"room": "bedroom"}).json()["sightings"]
    assert [x["item"] for x in s] == ["wallet"]


def test_where_never_seen(client):
    r = client.get("/api/where", params={"item": "wallet"}).json()
    assert r["found"] is False and r["say"] == "I haven't seen your wallet yet."


def test_where_unknown_item(client):
    r = client.get("/api/where", params={"item": "unicorn"}).json()
    assert r["found"] is False and r["say"] == "I haven't seen your unicorn yet."
    bad = client.get("/api/where", params={"item": "???"}).json()
    assert bad["found"] is False and "which item" in bad["say"]


def test_where_after_observe_with_alias(client, jpeg):
    ts = (datetime.now(timezone.utc) - timedelta(minutes=4)).isoformat()
    client.post("/api/observe", files=frame(jpeg), data={"room": "bedroom", "ts": ts})
    r = client.get("/api/where", params={"item": "my wallet"}).json()
    assert r["found"] and r["last"]["room"] == "bedroom"
    assert round(r["last"]["minutes_ago"]) == 4
    assert r["say"] == "Last seen at demo desk in your bedroom, about 4 minutes ago."
    assert client.get("/api/where", params={"item": "meds"}).json()["item"] == "medication"


def test_where_history_capped_at_five(client, jpeg):
    for _ in range(7):
        client.post("/api/observe", files=frame(jpeg), data={"room": "kitchen"})
    r = client.get("/api/where", params={"item": "keys"}).json()
    assert len(r["history"]) == 5


def test_guide_found_with_forced_x(client, jpeg):
    r = client.post("/api/guide", files=frame(jpeg), data={"item": "wallet"},
                    headers={"X-Mock-Item-X": "0.8"}).json()
    assert r["found"] is True
    assert r["angle"] == 19.5 and r["clock"] == 1 and r["pan"] == 0.65
    assert r["distance"] == "near" and r["beep_ms"] == 350
    assert r["say"] == "Wallet at 1 o'clock"
    assert len(r["point"]) == 2 and len(r["box"]) == 4


def test_guide_reach_and_query_param(client, jpeg):
    r = client.post("/api/guide?mock_x=0.5&mock_h=0.4", files=frame(jpeg),
                    data={"item": "keys", "yaw_deg": "12.5"}).json()
    assert r["distance"] == "reach" and r["beep_ms"] == 150 and r["say"] == "Reach out now"


def test_guide_only_speaks_on_change(client, jpeg):
    h = {"X-Mock-Item-X": "0.8"}
    first = client.post("/api/guide", files=frame(jpeg), data={"item": "wallet"}, headers=h).json()
    second = client.post("/api/guide", files=frame(jpeg), data={"item": "wallet"}, headers=h).json()
    assert first["say"] and second["say"] == ""


def test_guide_unknown_item(client, jpeg):
    r = client.post("/api/guide", files=frame(jpeg), data={"item": "unicorn"}).json()
    assert r["found"] is False and r["beep_ms"] == 0
    bad = client.post("/api/guide", files=frame(jpeg), data={"item": "   "}).json()
    assert bad["found"] is False and "which item" in bad["say"]


def test_guide_on_demand_custom_item_with_hint(client, jpeg):
    r = client.post("/api/guide", files=frame(jpeg), data={"item": "my water bottle"},
                    headers={"X-Mock-Item-X": "0.8"}).json()
    assert r["found"] is True and r["clock"] == 1 and "Water bottle" in r["say"]


def test_guide_item_not_in_watchlist_is_lost(client, jpeg):
    client.post("/api/profile", json={"watchlist": [{"name": "wallet"}], "rooms": ["bedroom"],
                                      "guidance_style": "clock_face"})
    r = client.post("/api/guide", files=frame(jpeg), data={"item": "keys"}).json()
    assert r["found"] is False


def test_websocket_receives_events(client, jpeg):
    with client.websocket_connect("/ws/events") as ws:
        client.post("/api/observe", files=frame(jpeg), data={"room": "bedroom"})
        ev = ws.receive_json()
        assert ev["type"] == "sighting" and ev["mock"] is True
        assert len(ev["sightings"]) == 5 and ev["frame"].startswith("data:image/jpeg")
        client.get("/api/where", params={"item": "wallet"})
        assert ws.receive_json()["type"] == "say"
        client.post("/api/guide", files=frame(jpeg), data={"item": "wallet"})
        assert ws.receive_json()["type"] == "guide"
        assert ws.receive_json()["type"] == "say"
