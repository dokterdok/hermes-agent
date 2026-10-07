"""Succession evidence preserves actual Discussion task identities and malformed-key uncertainty."""
from gateway import hosted_room_discussion as discussion, hosted_rooms
from gateway.platforms.api_server_run_idempotency import RunIdempotencyStore
from gateway.platforms.api_server_run_scope import room_run_scope_key


def test_room_evidence_keeps_complete_generated_task_identity_and_flags_bad_keys(tmp_path):
    room_db = tmp_path / 'room.db'
    room = hosted_rooms.create_room(room_db, room_id='room', name='Evidence', authority_gateway_id='home',
        members=[{'member_id': 'participant', 'profile': 'default', 'handle': 'one'},
                 {'member_id': 'other', 'profile': 'ops', 'handle': 'other'}])
    event = hosted_rooms.append_event(room_db, room_id='room', event_id='request', kind='message.user',
        actor={'kind': 'user', 'id': 'owner'}, authority_gateway_id='home', authority_epoch=1,
        payload={'text': '@one inspect the plan', 'thread_id': 'thread'})
    decision = discussion.plan_next_task(room, [event], local_profiles=('default', 'ops'))
    assert decision.task is not None
    generated = decision.task.identity.task_id
    assert ':' in generated
    identity = dict(room_id='room', home_install_id='home', authority_gateway_id='home', authority_epoch=1,
                    member_id=decision.task.member.member_id, target_install_id='participant', target_profile='default')
    scope = room_run_scope_key(identity)
    store = RunIdempotencyStore(str(tmp_path / 'runs.db'))
    expected = {'generated': (generated, 1), 'nested': (generated + ':subtask', 7), 'legacy': ('simple-task', 3)}
    try:
        for run_id, (task_id, generation) in expected.items():
            store.reserve(scope, f'room:{task_id}:{generation}', 'fingerprint', run_id,
                          {'run_id': run_id, 'status': 'completed'}, identity=identity)
        evidence = store.room_run_evidence('room', through_epoch=1)
        found = {run['run_id']: (run['task_id'], run['execution_generation']) for run in evidence['runs']}
        assert found == expected and evidence['truncated'] is False

        malformed = [f'other:{generated}:1', 'room::1', 'room:missing-generation',
                     f'room:{generated}:', f'room:{generated}:bad', f'room:{generated}:-1',
                     f'room:{generated}:0', 'room:simple-task:²']
        for index, key in enumerate(malformed):
            run_id = f'malformed-{index}'
            store.reserve(scope, key, 'fingerprint', run_id,
                          {'run_id': run_id, 'status': 'completed'}, identity=identity)
        evidence = store.room_run_evidence('room', through_epoch=1)
        found = {run['run_id']: (run['task_id'], run['execution_generation']) for run in evidence['runs']}
        assert found == expected and evidence['truncated'] is True
    finally:
        store.close()
