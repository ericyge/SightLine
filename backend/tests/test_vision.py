"""Exercise the installed SDK over an offline HTTP transport, including bad responses."""
import base64
import io
import json

import httpx
import pytest
from PIL import Image
from perceptron import Client

from app.frames import InvalidFrame, prepare_frame
from app.vision import PerceptronVision, VisionUnavailable

ITEMS = [{"name": "wallet", "description": "red leather wallet", "home_spot": "secret home"}]
LABEL = {"item": "wallet", "box": [420, 550, 580, 700], "spot": "on the desk beside the lamp"}


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.delenv("PERCEPTRON_REFERENCES", raising=False)
    clients = []

    def make(body=None, *, status=200, finish="stop", text=None, handler=None):
        requests = []

        def respond(request):
            requests.append(json.loads(request.content) if request.content else {})
            if handler:
                return handler(request)
            if status != 200:
                return httpx.Response(status, json={"error": {"message": "private provider detail"}})
            value = json.dumps(body if body is not None else {"detections": [LABEL]})
            return httpx.Response(200, json={"id": "offline", "object": "chat.completion",
                "model": "perceptron-mk1.5", "choices": [{"index": 0,
                "message": {"role": "assistant", "content": value if text is None else text},
                "finish_reason": finish}]})

        http = httpx.Client(transport=httpx.MockTransport(respond))
        sdk = Client(api_key="offline-test-only", provider="perceptron", http_client=http)
        vision = PerceptronVision(client=sdk)
        clients.append((vision, http))
        return vision, requests

    yield make
    for vision, http in clients:
        vision.close()
        http.close()


def test_sdk_request_and_normalized_labels(provider, jpeg):
    vision, requests = provider()
    result = vision.detect(jpeg, ITEMS, "bedroom")
    assert result[0].box == [.42, .55, .58, .7]
    assert result[0].confidence is None
    assert result[0].spot == LABEL["spot"]
    body = requests[0]
    assert body["model"] == "perceptron-mk1.5"
    assert body["response_format"]["json_schema"]["strict"] is True
    schema = body["response_format"]["json_schema"]["schema"]
    assert schema["$defs"]["_Label"]["properties"]["item"]["enum"] == ["wallet"]
    assert "secret home" not in json.dumps(body)
    image = body["messages"][-1]["content"][-1]["image_url"]["url"]
    assert base64.b64decode(image.split(",", 1)[1]) == jpeg


def test_empty_result_is_success(provider, jpeg):
    vision, _ = provider({"detections": []})
    assert vision.detect(jpeg, ITEMS) == []


def test_empty_watchlist_makes_no_call(provider, jpeg):
    vision, requests = provider()
    assert vision.detect(jpeg, []) == []
    assert not requests


SURVEY = {"decision": "new", "place_id": None, "name": "counter", "room": "kitchen",
          "description": "Counter beside the sink.", "landmarks": ["sink", "built-in cabinets"],
          "matched_landmarks": [], "instruction_from_previous": "Follow the doorway to the sink.",
          "transition_observed": True, "departure_view": "Facing the sink through the doorway.",
          "arrival_view": "Facing the sink beside the counter.", "reason": "Visible connected doorway."}


def test_survey_sends_previous_then_current_image_with_structured_transition(provider, jpeg):
    vision, requests = provider(SURVEY)
    previous = {"id": "doorway", "name": "doorway", "room": "kitchen"}
    result = vision.survey_place(jpeg, [], previous=previous, previous_frame=jpeg)
    assert result.transition_observed and result.departure_view == SURVEY['departure_view']
    body = requests[0]
    images = [part for part in body['messages'][-1]['content'] if part.get('type') == 'image_url']
    assert len(images) == 2
    assert body['messages'][-1]['content'][-1] == images[-1]
    schema = body['response_format']['json_schema']['schema']
    assert 'transition_observed' in schema['required']


def test_survey_cannot_claim_transition_without_previous_image(provider, jpeg):
    vision, _ = provider(SURVEY)
    result = vision.survey_place(jpeg, [], previous={'id': 'doorway', 'name': 'doorway', 'room': 'kitchen'})
    assert result.decision == 'new' and not result.transition_observed
    assert result.instruction_from_previous is None


def test_invalid_survey_response_fails_without_saving(provider, jpeg):
    vision, _ = provider({**SURVEY, 'transition_observed': 'yes'})
    with pytest.raises(VisionUnavailable, match='invalid place survey'):
        vision.survey_place(jpeg, [])


@pytest.mark.parametrize("box", [
    [-1, 0, 500, 500], [0, 0, 1001, 500], [500, 0, 400, 500],
    [0, 500, 500, 500], [0, 0, 500], [False, 0, 500, 500],
    ["0", 0, 500, 500], [0, 0, float("nan"), 500],
])
def test_bad_boxes_fail_without_fabricated_detection(provider, jpeg, box):
    vision, _ = provider({"detections": [{**LABEL, "box": box}]})
    with pytest.raises(VisionUnavailable, match="invalid labels"):
        vision.detect(jpeg, ITEMS)


@pytest.mark.parametrize("body", [
    {"detections": [{**LABEL, "item": "unicorn"}]},
    {"detections": [LABEL, LABEL]},
    {"detections": [{**LABEL, "confidence": .99}]},
    {"detections": [{"item": "wallet", "box": [0, 0, 500, 500]}]},
])
def test_unknown_duplicate_or_malformed_labels(provider, jpeg, body):
    vision, _ = provider(body)
    with pytest.raises(VisionUnavailable):
        vision.detect(jpeg, ITEMS)


@pytest.mark.parametrize("finish", ["length", "content_filter", "interrupted"])
def test_partial_completion_rejected(provider, jpeg, finish):
    vision, _ = provider(finish=finish)
    with pytest.raises(VisionUnavailable, match="incomplete"):
        vision.detect(jpeg, ITEMS)


def test_non_json_rejected(provider, jpeg):
    vision, _ = provider(text="I see a wallet")
    with pytest.raises(VisionUnavailable, match="invalid labels"):
        vision.detect(jpeg, ITEMS)


@pytest.mark.parametrize("status", [401, 404, 429, 500])
def test_provider_error_is_sanitized(provider, jpeg, status):
    vision, _ = provider(status=status)
    with pytest.raises(VisionUnavailable) as error:
        vision.detect(jpeg, ITEMS)
    assert "private provider detail" not in str(error.value)


def test_timeout_is_not_empty_result(provider, jpeg):
    def fail(request):
        raise httpx.ReadTimeout("private", request=request)
    vision, _ = provider(handler=fail)
    with pytest.raises(VisionUnavailable):
        vision.detect(jpeg, ITEMS)


def test_reference_photos_precede_current_frame(provider, jpeg, tmp_path, monkeypatch):
    photo = tmp_path / "wallet.jpg"
    photo.write_bytes(jpeg)
    manifest = tmp_path / "references.json"
    manifest.write_text(json.dumps({"wallet": ["wallet.jpg"]}))
    monkeypatch.setenv("PERCEPTRON_REFERENCES", str(manifest))
    vision, requests = provider()
    vision.detect(jpeg, ITEMS)
    content = requests[0]["messages"][-1]["content"]
    assert len([x for x in content if x["type"] == "image_url"]) == 2
    assert content[-1]["type"] == "image_url"


def test_model_discovery_uses_sdk(provider):
    def models(request):
        assert request.url.path.endswith("/models")
        return httpx.Response(200, json={"object": "list", "data": [
            {"id": "perceptron-mk1.5", "object": "model"}]})
    vision, _ = provider(handler=models)
    assert vision.available_models() == ["perceptron-mk1.5"]


def test_frame_resizes_and_orients_without_disk():
    original = Image.new("RGB", (1200, 800))
    exif = Image.Exif()
    exif[274] = 6
    data = io.BytesIO()
    original.save(data, "JPEG", exif=exif)
    result = Image.open(io.BytesIO(prepare_frame(data.getvalue())))
    assert result.size == (427, 640)
    assert not result.getexif()


def test_bad_image_rejected():
    with pytest.raises(InvalidFrame):
        prepare_frame(b"not an image")


def test_api_persists_real_labels_and_unknown_confidence(client, provider, jpeg, monkeypatch):
    from app import main
    vision, _ = provider()
    monkeypatch.setattr(main, "get_vision", lambda: vision)
    response = client.post("/api/observe", files={"frame": ("frame.jpg", jpeg)},
                           data={"room": "bedroom", "ts": "2026-10-17T12:00:00-07:00", "auto_place": "false"})
    assert response.status_code == 200
    saved = response.json()["sightings"][0]
    assert saved["confidence"] is None
    assert saved["ts"] == "2026-10-17T19:00:00+00:00"
    assert client.get(saved["thumb"]).status_code == 200
    assert client.get("/api/where?item=wallet").json()["last"]["confidence"] is None
    guide = client.post("/api/guide", files={"frame": ("frame.jpg", jpeg)}, data={"item": "wallet"})
    assert guide.status_code == 200 and guide.json()["found"]


def test_failed_observation_leaves_memory_intact(client, provider, jpeg, monkeypatch):
    from app import main
    upload = {"frame": ("frame.jpg", jpeg)}
    client.post("/api/observe", files=upload, data={"room": "bedroom"})
    before = client.get("/api/where?item=wallet").json()["history"]
    vision, _ = provider(status=429)
    monkeypatch.setattr(main, "get_vision", lambda: vision)
    assert client.post("/api/observe", files=upload, data={"room": "kitchen"}).status_code == 503
    assert client.post("/api/guide", files=upload, data={"item": "wallet"}).status_code == 503
    assert client.get("/api/where?item=wallet").json()["history"] == before


def test_bad_upload_and_timestamp_return_422(client, jpeg):
    assert client.post("/api/observe", files={"frame": ("x.jpg", b"broken")},
                       data={"room": "bedroom"}).status_code == 422
    assert client.post("/api/observe", files={"frame": ("x.jpg", jpeg)},
                       data={"room": "bedroom", "ts": "yesterday"}).status_code == 422


AUTO_LOCATION = {"decision": "new", "local_key": "counter", "candidate_id": None,
    "name": "counter beside sink", "room": "kitchen", "description": "Counter between sink and cabinets.",
    "landmarks": ["Steel sink on the left", "White built-in cabinets above"],
    "matched_landmarks": [], "reason": "Two fixed landmarks are visible."}


def test_auto_observation_uses_one_structured_request_and_supplied_context(provider, jpeg):
    vision, requests = provider({"detections": [{**LABEL, "location": AUTO_LOCATION}]})
    context = {"places": [], "catalog_complete": True, "recent_observations": []}
    labels = vision.observe(jpeg, ITEMS, None, context)
    assert labels[0].location.name == AUTO_LOCATION["name"]
    assert labels[0].box == [.42, .55, .58, .7] and labels[0].confidence is None
    assert len(requests) == 1
    body = requests[0]
    schema = body["response_format"]["json_schema"]["schema"]
    assert "location" in schema["$defs"]["_LocatedLabel"]["required"]
    assert "secret home" not in json.dumps(body)
    content = body["messages"][-1]["content"]
    supplied = json.loads(content[-3]["text"])
    assert supplied["task"] == "memory" and supplied["room_hint"] is None
    assert supplied["catalog_complete"] and supplied["recent_observations"] == []
    assert content[-1]["type"] == "image_url"


def test_api_saves_automatic_places_from_sdk_response(client, provider, jpeg, monkeypatch):
    from app import main
    vision, requests = provider({"detections": [{**LABEL, "location": AUTO_LOCATION}]})
    monkeypatch.setattr(main, "get_vision", lambda: vision)
    response = client.post("/api/observe", files={"frame": ("frame.jpg", jpeg)})
    assert response.status_code == 200 and len(requests) == 1
    saved = response.json()["sightings"][0]
    assert saved["place_source"] == "automatic" and saved["room"] == "kitchen"
    assert client.get("/api/where?item=wallet").json()["destination"]["id"] == saved["place_id"]


@pytest.mark.parametrize("location", [
    {**AUTO_LOCATION, "decision": "certain"},
    {**AUTO_LOCATION, "confidence": .99},
    {**AUTO_LOCATION, "matched_landmarks": [False, 1]},
    {**AUTO_LOCATION, "landmarks": [" " ]},
])
def test_malformed_auto_response_saves_nothing(client, provider, jpeg, monkeypatch, location):
    from app import main
    from app import memory
    vision, _ = provider({"detections": [{**LABEL, "location": location}]})
    monkeypatch.setattr(main, "get_vision", lambda: vision)
    assert client.post("/api/observe", files={"frame": ("frame.jpg", jpeg)}).status_code == 503
    assert memory.get_map()["places"] == []
    assert not list(main.db.thumbs_dir().glob("*.jpg"))


def test_null_location_is_valid_object_detection(provider, jpeg):
    vision, _ = provider({"detections": [{**LABEL, "location": None}]})
    labels = vision.observe(jpeg, ITEMS, None, {"places": []})
    assert labels[0].location is None and labels[0].item == "wallet"


def test_open_vocabulary_observation_and_on_demand_guidance(client, provider, jpeg, monkeypatch):
    from app import main
    from app import memory
    open_detections = [
        {"item": "a coffee mug", "box": [100, 500, 220, 650], "spot": "on the counter beside the sink",
         "location": AUTO_LOCATION},
        {"item": "water bottle", "box": [600, 480, 720, 680], "spot": "on the counter beside the sink",
         "location": AUTO_LOCATION},
        {"item": "chapstick", "box": [300, 520, 350, 580], "spot": "on the counter beside the sink",
         "location": AUTO_LOCATION},
    ]
    vision, requests = provider({"detections": open_detections})
    monkeypatch.setattr(main, "get_vision", lambda: vision)
    res = client.post("/api/observe", files={"frame": ("frame.jpg", jpeg)})
    assert res.status_code == 200
    saved_items = [s["item"] for s in res.json()["sightings"]]
    assert saved_items == ["cup", "water bottle", "chapstick"]
    schema = requests[0]["response_format"]["json_schema"]["schema"]
    assert "enum" not in schema["$defs"]["_LocatedLabel"]["properties"]["item"]

    # Query via synonyms ("my mug", "my bottle") and arbitrary name ("my chapstick")
    for query, expected in [("my mug", "cup"), ("a cup", "cup"), ("my bottle", "water bottle"),
                            ("water bottle", "water bottle"), ("my chapstick", "chapstick")]:
        where = client.get("/api/where", params={"item": query}).json()
        assert where["found"] is True and where["item"] == expected
        assert where["destination"]["name"] == AUTO_LOCATION["name"]

    recalled = {entry["item"] for entry in memory.recalled_items()}
    assert {"cup", "water bottle", "chapstick"} <= recalled

    # On-demand guidance works for open-vocabulary items not in the profile watchlist
    guide_vision, guide_reqs = provider({"detections": [{"item": "water bottle", "box": [700, 550, 820, 700], "spot": ""}]})
    monkeypatch.setattr(main, "get_vision", lambda: guide_vision)
    guide = client.post("/api/guide", files={"frame": ("frame.jpg", jpeg)}, data={"item": "my bottle"}).json()
    assert guide["found"] is True and "Water bottle" in guide["say"]
    assert guide_reqs[0]["response_format"]["json_schema"]["schema"]["$defs"]["_Label"]["properties"]["item"]["enum"] == ["water bottle"]


def test_calibration_open_vocabulary_without_any_watchlist_object(client, provider, jpeg, monkeypatch):
    from datetime import datetime, timezone
    from app import main
    from app.vision import MockVision
    # Survey is scripted; the real SDK/observation adapter handles an offline provider response.
    vision, requests = provider({"detections": [
        {"item": "a coffee mug", "box": [100, 500, 220, 650], "spot": "on the counter", "location": AUTO_LOCATION},
        {"item": "stapler", "box": [300, 520, 350, 580], "spot": "on the counter", "location": AUTO_LOCATION},
    ]})
    monkeypatch.setattr(vision, 'survey_place', MockVision().survey_place)
    monkeypatch.setattr(main, 'get_vision', lambda: vision)
    session = client.post('/api/calibration/start').json()['session_id']
    response = client.post('/api/calibration/frame', files={'frame': ('frame.jpg', jpeg, 'image/jpeg')},
        data={'session_id': session, 'sequence': 1, 'captured_at': datetime.now(timezone.utc).isoformat()})
    assert response.status_code == 200
    assert [s['item'] for s in response.json()['sightings']] == ['cup', 'stapler']
    request = requests[0]
    schema = request['response_format']['json_schema']['schema']
    assert 'enum' not in schema['$defs']['_LocatedLabel']['properties']['item']
    assert 'no watched objects are visible' not in request['messages'][0]['content']
    assert 'not an exhaustive list' in request['messages'][0]['content']
    for query, expected in [('my mug', 'cup'), ('my stapler', 'stapler')]:
        answer = client.get('/api/where', params={'item': query}).json()
        assert answer['found'] and answer['item'] == expected and answer['destination']
    print('\nOpen-vocabulary calibration: cup and stapler saved and recalled; neither is on the watchlist.')


@pytest.mark.parametrize('status', ['aligned', 'left', 'right', 'blocked', 'unknown'])
def test_departure_in_localization_request(provider, jpeg, status):
    place = {'id': 'door', 'name': 'doorway', 'room': 'bedroom', 'description': 'Bedroom exit.',
             'landmarks': ['doorframe', 'built-in shelf'],
             'departure': {'to_place_id': 'hall', 'departure_view': 'Facing the hallway with shelf on right.'}}
    vision, requests = provider({'place_id': 'door', 'matched_landmarks': [0, 1],
                                 'reason': 'Doorframe and shelf visible.', 'departure_status': status})
    result = vision.localize(jpeg, [place], {'facing': 'aligned'})
    assert result.departure_status == status  # Real provider ignores mock hint.
    assert result.place_id == 'door'
    assert len(requests) == 1
    body = requests[0]
    assert 'departure_status' in body['response_format']['json_schema']['schema']['required']
    assert 'Facing the hallway with shelf on right.' in json.dumps(body)
    assert 'knowing a room does NOT establish facing' in body['messages'][0]['content']


@pytest.mark.parametrize('updates', [{'departure_status': 'forward'}, {'matched_landmarks': [0]},
                                    {'place_id': 'invented'}, {'matched_landmarks': [0, 0]}])
def test_departure_rejects_invalid_or_weak_evidence(provider, jpeg, updates):
    place = {'id': 'door', 'name': 'door', 'room': 'bedroom', 'description': 'Exit.',
             'landmarks': ['frame', 'shelf'], 'departure': {'departure_view': 'Facing hallway.'}}
    body = {'place_id': 'door', 'matched_landmarks': [0, 1], 'reason': 'Visible.',
            'departure_status': 'aligned', **updates}
    vision, _ = provider(body)
    if updates.get('departure_status') == 'forward':
        with pytest.raises(VisionUnavailable):
            vision.localize(jpeg, [place])
    else:
        result = vision.localize(jpeg, [place])
        assert result.place_id is None and result.departure_status == 'unknown'


def test_departure_field_required_and_plain_localization_compatible(provider, jpeg):
    place = {'id': 'door', 'name': 'door', 'room': 'bedroom', 'description': 'Exit.',
             'landmarks': ['frame', 'shelf']}
    vision, _ = provider({'place_id': 'door', 'matched_landmarks': [0, 1], 'reason': 'Visible.'})
    assert vision.localize(jpeg, [place]).place_id == 'door'
    with pytest.raises(VisionUnavailable):
        vision.localize(jpeg, [{**place, 'departure': {'departure_view': 'Facing hallway.'}}])
