"""Facing checkpoint: simulated views, no real apartment data or provider calls."""
import json
from pathlib import Path

import pytest

from app import db, main
from app.vision import get_vision, VisionUnavailable
from app.vision.vision import _Localization, _accept_localization


@pytest.fixture
def apartment(client):
    path = Path(__file__).resolve().parents[1] / 'examples' / 'two_room_memory.json'
    assert client.post('/api/memory/map', json=json.loads(path.read_text())).status_code == 200
    db.add_sighting('wallet', 'kitchen', 'on counter', [.1, .2, .3, .4], None,
                    '2026-10-17T20:00:00Z', '', place_id='kitchen_counter')
    return client


def guide(client, jpeg, facing=None, place='bedroom_door', visible=False):
    headers = {'X-Mock-Place': place}
    if facing is not None:
        headers['X-Mock-Facing'] = facing
    if not visible:
        headers['X-Mock-Hide'] = '1'
    return client.post('/api/guide', data={'item': 'wallet'}, headers=headers,
                       files={'frame': ('frame.jpg', jpeg, 'image/jpeg')})


@pytest.mark.parametrize('facing,phrase', [(None, 'scan in place'), ('unknown', 'scan in place'),
    ('left', 'left in place'), ('right', 'right in place'), ('blocked', 'Stop.'),
    ('invented', 'scan in place')])
def test_uncertain_facing_never_releases_walking_instruction(apartment, jpeg, facing, phrase):
    response = guide(apartment, jpeg, facing)
    assert response.status_code == 200
    result = response.json()
    assert result['mode'] == 'navigate' and result['current_place'] == 'bedroom_door'
    assert result['destination']['id'] == 'kitchen_counter'
    assert result['next_instruction'] is None and result['beep_ms'] == 0
    assert not result['found'] and phrase in result['say']
    assert guide(apartment, jpeg, facing).json()['say'] == ''
    print(f"\nFacing {facing}: {result['say']}")


def test_turn_align_block_recover_advance_arrive_reacquire(apartment, jpeg):
    for facing in ['left', 'aligned', 'blocked', 'aligned']:
        result = guide(apartment, jpeg, facing).json()
        assert result['say']  # Recovery repeats walking instruction, uncertainty interrupts it.
        assert bool(result['next_instruction']) == (facing == 'aligned')
        print(f"\n{facing}: {result['say']}")
    next_room = guide(apartment, jpeg, 'unknown', 'kitchen_entry').json()
    assert next_room['next_instruction'] is None
    aligned = guide(apartment, jpeg, 'aligned', 'kitchen_entry').json()
    assert aligned['next_instruction'] == 'Approach the counter beside the sink.'
    arrival = guide(apartment, jpeg, 'unknown', 'kitchen_counter').json()
    assert arrival['next_instruction'] is None and 'Look around slowly' in arrival['say']
    assert arrival['departure_status'] is None
    visible = guide(apartment, jpeg, 'blocked', 'kitchen_counter', visible=True).json()
    assert visible['mode'] == 'visual' and visible['found']
    assert visible['departure_status'] is None


def test_provider_failure_does_not_reuse_alignment(apartment, jpeg, monkeypatch):
    assert guide(apartment, jpeg, 'aligned').json()['next_instruction']
    vision = get_vision()
    original = vision.localize
    def fail(*args, **kwargs):
        raise VisionUnavailable('Camera check unavailable.')
    monkeypatch.setattr(vision, 'localize', fail)
    assert guide(apartment, jpeg, 'aligned').status_code == 503
    assert main._navigate_states['wallet'].last_key is None
    monkeypatch.setattr(vision, 'localize', original)
    assert guide(apartment, jpeg, 'aligned').json()['say']


def test_facing_cannot_override_unknown_location(apartment, jpeg):
    result = guide(apartment, jpeg, 'aligned', 'unknown').json()
    assert result['mode'] == 'lost' and result['next_instruction'] is None
    assert result['departure_status'] is None


def test_localization_rejects_shared_or_duplicate_landmarks():
    place = {'id': 'one', 'landmarks': ['sink', 'cabinet']}
    raw = _Localization(place_id='one', matched_landmarks=[0, 1], reason='Visible fixtures.')
    assert _accept_localization(raw, [place, {'id': 'two', 'landmarks': ['Sink', 'CABINET']}]).place_id is None
    assert _accept_localization(raw, [{'id': 'one', 'landmarks': ['sink', 'SINK']}]).place_id is None
