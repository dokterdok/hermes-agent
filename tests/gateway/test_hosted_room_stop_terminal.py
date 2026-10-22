"""A local Stop request is not evidence that its canonical producer ended."""
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

from gateway import hosted_room_driver as tasks, hosted_rooms as rooms
from gateway.session_hosted_service import CanonicalHostedRoomService
from hermes_state_runtime import claim_session_input, get_session_admission, settle_session_input


def _turn(hosted_owner, monkeypatch, *, started=True):
    """One planned room turn for a local Bot; ``started`` claims its canonical admission."""
    authority, loop, _, agent = hosted_owner
    service = CanonicalHostedRoomService(authority, loop)
    monkeypatch.setattr(service, 'profile_homes', lambda: {
        'default': Path(authority.profile_id), 'other': Path(authority.profile_id) / 'other'})
    service.authorize_room('alice', 'room', create=True)
    rooms.create_room(authority.db.db_path, room_id='room', name='Room',
        authority_gateway_id=rooms.local_authority_gateway_id(),
        members=[{'member_id': 'one', 'profile': 'default', 'handle': 'one'},
                 {'member_id': 'two', 'profile': 'other', 'handle': 'two'}])
    service.send(room_id='room', event_id='input', payload={'text': '@one input', 'thread_id': 'thread'})
    (queued,) = tasks.list_tasks(authority.db.db_path, room_id='room', status='queued')
    binding = service.bindings()[0]
    lease = service.runtime._ensure_lease(binding)
    attempt = tasks.start_task(authority.db.db_path, queued['identity'], lease, expected_cancel_generation=0,
                               clock=time.time)
    member = service._resolve_member_transport(binding, tasks.get_task(authority.db.db_path, attempt.identity))
    coords = dict(profile='default', source='bot_room')
    sid = member.create(**coords, title='Group: room')['session_id']
    receipt = member.submit(**coords, session_id=sid, prompt='input', task=attempt.identity,
        execution_generation=attempt.execution_generation,
        on_terminal=lambda result: service.runtime._on_terminal(binding, attempt, result))
    if started:
        claim_session_input(authority.db, epoch=authority.epoch, session_id=sid)
    turn = SimpleNamespace(authority=authority, service=service, binding=binding, lease=lease,
                           identity=attempt.identity, agent=agent)
    turn.task = lambda: tasks.get_task(authority.db.db_path, attempt.identity)
    turn.admission = lambda: get_session_admission(authority.db, admission_id=receipt['admission_id'])
    return turn


def _finish(turn, outcome, text=''):
    """The Bot's turn commits its outcome."""
    canonical = turn.admission()
    settle_session_input(turn.authority.db, epoch=turn.authority.epoch, admission_id=canonical['admission_id'],
        generation=canonical['generation'], outcome=outcome,
        result={'result': {'interrupted': outcome == 'interrupted', 'final_response': text}, 'usage': {}})


@pytest.mark.parametrize('producer_state', ['queued', 'started'])
def test_room_stop_waits_for_the_producer_terminal(hosted_owner, monkeypatch, producer_state):
    turn = _turn(hosted_owner, monkeypatch, started=producer_state == 'started')
    turn.service.stop_room('room', cancel_id='stop')  # what groups.stop runs
    if producer_state == 'queued':
        # Keeps working: a turn that never started is stopped at once.
        assert turn.task()['status'] == 'cancelled'
        assert (turn.admission()['status'], turn.admission()['outcome']) == ('terminal', 'cancelled')
        assert not turn.agent.interrupted
        return
    assert turn.agent.interrupted and turn.admission()['status'] == 'started'
    assert turn.task()['status'] == 'stopping', {
        'hosted_status': turn.task()['status'], 'canonical_status': turn.admission()['status']}
    _finish(turn, 'interrupted')
    turn.service.stop_room('room', cancel_id='stop')
    assert turn.task()['status'] == 'cancelled'


def test_end_does_not_wait_for_a_local_turn_to_wind_down(hosted_owner, monkeypatch):
    from gateway.session_group_controls import _group
    turn = _turn(hosted_owner, monkeypatch)
    turn.authority.hosted_room_service = turn.service
    monkeypatch.setattr(turn.service.runtime, 'status', lambda: {'running': True, 'stopping': False})
    ended = _group(turn.authority, SimpleNamespace(subject='alice'), 'groups.disband',
                   {'room_id': 'room', 'cancel_id': 'end'})
    # End delivers the Stop and deletes the room at once, as before; only groups.stop waits.
    assert ended['tombstone']['room_id'] == 'room'
    assert turn.agent.interrupted and turn.task()['status'] == 'cancelled'


def test_a_stop_after_the_reply_lands_publishes_it_without_stalling_the_owner(hosted_owner, monkeypatch):
    turn = _turn(hosted_owner, monkeypatch)
    turn.service.stop_room('room', cancel_id='stop-1')
    _finish(turn, 'completed', 'the answer')
    # A second Stop reads the session while the owner loop publishes that reply under the
    # room's policy lock: Stop must not hold the lock across that read.
    turn.service.stop_room('room', cancel_id='stop-2')
    texts = [e['payload'].get('text') for e in rooms.read_events(
        turn.authority.db.db_path, room_id='room', limit=100)['events']]
    assert 'the answer' in texts and turn.task()['status'] == 'settled'
    assert turn.service.runtime.status()['last_error'] is None


def test_a_stop_retry_keeps_the_deadline_failure(hosted_owner, monkeypatch):
    turn = _turn(hosted_owner, monkeypatch)
    deadline = tasks.begin_task_cancel(turn.authority.db.db_path, turn.identity, cancel_id='deadline:1',
                                       expected_cancel_generation=0, clock=time.time)
    assert not turn.service.runtime._finish_stop(turn.binding, deadline, turn.lease)  # still running
    _finish(turn, 'interrupted')
    turn.service.stop_room('room', cancel_id='user-stop')
    task = turn.task()
    assert (task['status'], (task['result'] or {}).get('reason_code')) == ('failed', 'turn_deadline_exceeded')
