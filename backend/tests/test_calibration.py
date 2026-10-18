"""Live calibration checkpoint: python -m pytest tests/test_calibration.py -q -s."""
from datetime import datetime, timedelta, timezone

import pytest

from app import db, main, memory
from app.memory import calibration
from app.vision import Detection, PlaceSurveyResult, VisionUnavailable


@pytest.fixture(autouse=True)
def clear_continuity():
    calibration.reset_continuity()
    yield
    calibration.reset_continuity()


def start(client):
    response = client.post('/api/calibration/start')
    assert response.status_code == 200
    result = response.json()
    print('\nCalibration:', result['say'])
    return result['session_id']


def send(client, jpeg, session, sequence, **kwargs):
    data = {'session_id': session, 'sequence': sequence,
            'captured_at': datetime.now(timezone.utc).isoformat()}
    data.update(kwargs.pop('data', {}))
    return client.post('/api/calibration/frame', files={'frame': ('frame.jpg', jpeg, 'image/jpeg')},
                       data=data, **kwargs)


def test_calibration_discovers_apartment_and_actual_return_paths(client, jpeg):
    assert client.get('/api/calibration').json()['status'] == 'idle'
    session = start(client)  # No starting place, room, or map supplied.
    for seq in range(1, 11):
        response = send(client, jpeg, session, seq)
        assert response.status_code == 200, response.text
        result = response.json()
        if result['say']:
            print('Frame', seq, result['say'])
        if seq == 6:
            assert len(result['places']) == 3 and result['connections_total'] == 2
            assert result['missing_paths'] and not result['coverage_ready']
            assert {s['item'] for s in result['sightings']} == {'wallet', 'cup', 'chapstick'}
            print('Remembered objects:', ', '.join(s['item'] for s in result['sightings']))
        if seq == 2:
            assert result['say'] == ''  # Repeated recognition is not spoken every frame.
    assert result['connections_total'] == 4 and result['coverage_ready']
    saved = client.post('/api/calibration/finish', json={'session_id': session}).json()
    assert saved['status'] == 'saved' and saved['current_place'] is None
    print('Finish:', saved['say'])
    assert client.get('/api/memory/map').json()['connections']
    assert client.get('/api/where?item=wallet').json()['found']
    assert client.get('/api/where?item=my%20chapstick').json()['found']
    assert client.get('/api/where?item=my%20mug').json()['item'] == 'cup'
    # Structured metadata survives reopening the database, never a presumed current position.
    db._initialized.discard(db.db_path())
    calibration.reset_continuity()
    restored = client.get('/api/calibration').json()
    assert restored['coverage_ready'] and restored['session_id'] == session
    assert restored['current_place'] is None


def test_finish_reports_incomplete_and_resume_retains_map(client, jpeg):
    session = start(client)
    for seq in range(1, 5):
        assert send(client, jpeg, session, seq).status_code == 200
    result = client.post('/api/calibration/finish', json={'session_id': session}).json()
    assert not result['coverage_ready'] and 'missing' in result['say']
    assert send(client, jpeg, session, 5).status_code == 409
    resumed = client.post('/api/calibration/start').json()
    assert resumed['session_id'] != session and len(resumed['places']) == 2
    assert resumed['current_place'] is None


def test_start_is_repeatable_and_does_not_erase_map(client, jpeg):
    session = start(client)
    send(client, jpeg, session, 1)
    result = client.post('/api/calibration/start').json()
    assert result['session_id'] == session and len(result['places']) == 1
    assert result['current_place'] is None


@pytest.mark.parametrize('break_kind', ['unknown', 'restart', 'sequence_gap', 'pause', 'provider_error'])
def test_no_connection_across_missing_continuity(client, jpeg, monkeypatch, break_kind):
    session = start(client)
    for seq in [1, 2]:
        assert send(client, jpeg, session, seq).status_code == 200
    if break_kind == 'unknown':
        unknown = send(client, jpeg, session, 3, headers={'X-Mock-Place': 'unknown'}).json()
        assert unknown['current_place'] is None and 'slowly scan' in unknown['say']
    elif break_kind == 'restart':
        calibration.reset_continuity()
    elif break_kind == 'pause':
        calibration._previous.expires_at = 0
    elif break_kind == 'provider_error':
        vision = main.get_vision()
        original = vision.survey_place
        def fail(*args, **kwargs):
            raise VisionUnavailable('Provider unavailable.')
        monkeypatch.setattr(vision, 'survey_place', fail)
        assert send(client, jpeg, session, 3).status_code == 503
        monkeypatch.setattr(vision, 'survey_place', original)
    response = send(client, jpeg, session, 4 if break_kind != 'restart' else 3)
    assert response.status_code == 200
    assert len(response.json()['places']) == 2
    assert response.json()['connections_total'] == 0


def test_duplicate_and_old_frames_never_add_sightings(client, jpeg):
    session = start(client)
    assert send(client, jpeg, session, 1).status_code == 200
    history = db.recent_sightings('wallet')
    assert send(client, jpeg, session, 1).status_code == 409
    assert db.recent_sightings('wallet') == history
    stale = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    assert send(client, jpeg, session, 2, data={'captured_at': stale}).status_code == 422
    assert db.recent_sightings('wallet') == history
    assert send(client, jpeg, 'missing', 2).status_code == 404


def test_new_sequence_with_old_capture_time_is_rejected(client, jpeg):
    session = start(client)
    now = datetime.now(timezone.utc)
    assert send(client, jpeg, session, 1, data={'captured_at': now.isoformat()}).status_code == 200
    old = (now - timedelta(seconds=1)).isoformat()
    assert send(client, jpeg, session, 2, data={'captured_at': old}).status_code == 409
    assert client.get('/api/calibration').json()['frames_processed'] == 1


def test_capture_gap_cannot_connect_places(client, jpeg):
    session = start(client)
    old = datetime.now(timezone.utc) - timedelta(seconds=20)
    for seq in [1, 2]:
        assert send(client, jpeg, session, seq, data={
            'captured_at': (old + timedelta(seconds=seq)).isoformat()}).status_code == 200
    result = send(client, jpeg, session, 3).json()
    assert len(result['places']) == 2 and result['connections_total'] == 0


def test_calibration_never_assigns_camera_place_to_every_object(client, jpeg, monkeypatch):
    monkeypatch.setattr(main.get_vision(), 'observe', lambda *args, **kwargs:
                        [Detection('wallet', [.1, .2, .3, .4], None, 'inside a bag')])
    result = send(client, jpeg, start(client), 1).json()
    assert result['current_place'] is not None
    assert result['sightings'][0]['place_id'] is None


def test_frame_validation_never_advances_session(client):
    session = start(client)
    response = client.post('/api/calibration/frame', files={'frame': ('bad.jpg', b'bad', 'image/jpeg')},
        data={'session_id': session, 'sequence': 1, 'captured_at': datetime.now(timezone.utc).isoformat()})
    assert response.status_code == 422
    assert client.get('/api/calibration').json()['last_sequence'] == 0


def test_busy_frame_is_rejected_without_queue(client, jpeg):
    session = start(client)
    with calibration._lock:
        assert send(client, jpeg, session, 1).status_code == 409
        assert client.post('/api/calibration/finish', json={'session_id': session}).status_code == 409
    assert client.get('/api/calibration').json()['last_sequence'] == 0


def test_fast_detect_only_mode_explains_missing_support(client, monkeypatch):
    vision = main.get_vision()
    monkeypatch.setattr(vision, 'mode', 'live')
    assert not client.get('/api/calibration').json()['available']
    result = client.post('/api/calibration/start')
    assert result.status_code == 503 and 'PERCEPTRON_MODE=perceptron' in result.json()['detail']


def test_failed_object_memory_does_not_erase_successful_map(client, jpeg, monkeypatch):
    def fail(*args, **kwargs):
        raise VisionUnavailable('Object observation unavailable.')
    monkeypatch.setattr(main.get_vision(), 'observe', fail)
    response = send(client, jpeg, start(client), 1)
    assert response.status_code == 200
    result = response.json()
    assert len(result['places']) == 1 and result['memory_warning']
    assert result['sightings'] == []


def test_missing_transition_evidence_never_invents_connection(client):
    first = memory.apply_survey(PlaceSurveyResult(decision='new', name='counter', room='kitchen',
        description='Counter by sink.', landmarks=['sink', 'tile backsplash']))
    second = memory.apply_survey(PlaceSurveyResult(decision='new', name='doorway', room='bedroom',
        description='Doorway by bed.', landmarks=['bedroom door frame', 'built-in closet'],
        instruction_from_previous='Turn left.'), previous_place_id=first.place_id)
    assert second.place_id and not second.connection_added
    assert memory.get_map()['connections'] == []


def test_unknown_survey_keeps_object_memory_independent(client, jpeg, monkeypatch):
    monkeypatch.setattr(main.get_vision(), 'observe', lambda *args, **kwargs:
                        [Detection('wallet', [.1, .2, .3, .4], None, 'inside a bag')])
    result = send(client, jpeg, start(client), 1, headers={'X-Mock-Place': 'unknown'}).json()
    assert result['current_place'] is None and result['sightings']
    assert all(s['place_id'] is None for s in result['sightings'])
