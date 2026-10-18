"""Continuous memory collapses stationary repeats without hiding moves or unknown places."""
from datetime import datetime, timedelta, timezone

import pytest

from app import db, memory
from app.memory.models import MemoryMapUpdate
from app.memory.observation import observe_and_save
from app.vision.vision import Detection


class Camera:
    spot = 'on the counter beside the sink'
    box = [.1, .2, .3, .4]
    def detect(self, *args):
        return [Detection('wallet', self.box, None, self.spot)]


@pytest.fixture
def setup(client):
    memory.teach_map(MemoryMapUpdate.model_validate({'places': [
        {'id': p, 'name': p, 'room': 'kitchen', 'description': p, 'landmarks': ['sink', p]}
        for p in ['counter', 'desk']]}))
    return Camera()


def observe(camera, jpeg, seconds, place='counter', session='phone', coalesce=True):
    ts = (datetime(2026, 10, 17, tzinfo=timezone.utc) + timedelta(seconds=seconds)).isoformat()
    return observe_and_save(camera, jpeg, 'kitchen', ts, place_id=place,
        auto_place=False, session_id=session, coalesce=coalesce)[0][0]


def test_stationary_run_refreshes_time_box_and_keeps_one_crop(setup, jpeg):
    first = observe(setup, jpeg, 0)
    setup.box = [.2, .3, .4, .5]  # Camera movement alone does not establish object movement.
    for seconds in range(1, 61):
        last = observe(setup, jpeg, seconds)
    assert first['id'] == last['id'] and last['ts'] != first['ts']
    assert last['box'] == setup.box
    assert len(db.recent_sightings('wallet', 100)) == 1
    assert first['thumb'] == last['thumb']
    assert len(list(db.thumbs_dir().glob('*.jpg'))) == 1


@pytest.mark.parametrize('change', ['place', 'spot', 'session', 'gap', 'older', 'unknown', 'no_session', 'disabled'])
def test_meaningful_changes_are_not_collapsed(setup, jpeg, change):
    first = observe(setup, jpeg, 20)
    kwargs = {}
    seconds = 21
    if change == 'place': kwargs['place'] = 'desk'
    if change == 'spot': setup.spot = 'inside a bag'
    if change == 'session': kwargs['session'] = 'other'
    if change == 'gap': seconds = 36
    if change == 'older': seconds = 19
    if change == 'unknown': kwargs['place'] = None
    if change == 'no_session': kwargs['session'] = None
    if change == 'disabled': kwargs['coalesce'] = False
    second = observe(setup, jpeg, seconds, **kwargs)
    assert first['id'] != second['id']
    assert len(db.recent_sightings('wallet')) == 2
    if change == 'unknown':
        assert db.recent_sightings('wallet')[0]['place_id'] is None
        # Newest is unassigned; route still uses the older mapped counter.
        assert memory.directions_to_item('wallet', from_place_id='counter')['status'] == 'already_there'


def test_return_to_previous_place_creates_new_event(setup, jpeg):
    first = observe(setup, jpeg, 0)
    observe(setup, jpeg, 1, place='desk')
    returned = observe(setup, jpeg, 2)
    assert returned['id'] != first['id']
    assert len(db.recent_sightings('wallet')) == 3


def test_transaction_rolls_back_refresh_when_another_detection_fails(setup, jpeg, monkeypatch):
    first = observe(setup, jpeg, 0)
    monkeypatch.setattr(setup, 'detect', lambda *args: [
        Detection('wallet', [.1, .2, .3, .4], None, setup.spot),
        Detection('keys', [.1], None, 'on counter')])
    with pytest.raises((IndexError, ValueError)):
        observe(setup, jpeg, 1)
    assert db.recent_sightings('wallet')[0] == first
    assert len(list(db.thumbs_dir().glob('*.jpg'))) == 1
