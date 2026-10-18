"""Positive checks for lane4a's authority/rewind scope and the receipt-held route."""
from dataclasses import replace

import pytest

from tests._review_probes.pkg10.lane4a.test_zz_probe_lane4a import append, local, mutation, rotate


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['ancestor', 'foreign', 'assistant', 'missing', 'compacted', 'rewound'])
async def test_canonical_rewind_rejects_every_nonlive_target(tmp_path, monkeypatch, kind):
    from gateway.session_mutations import mutate_session
    from hermes_state_runtime import RuntimeStoreError
    authority, actor, ref, _ = await local(tmp_path, monkeypatch)
    db = authority.db
    append(db, ref.session_id, 'OLD_Q', 'OLD_A', 'TAIL_Q', 'TAIL_A')
    ancestor = db.get_messages(ref.session_id)[0]['id']
    rotate(db, ref.session_id)
    append(db, 'child', 'NEW_Q', 'NEW_A')
    target = next(m['id'] for m in db.get_messages('child') if m['content'] == 'NEW_Q')
    if kind == 'ancestor':
        target = ancestor
    elif kind == 'foreign':
        db.create_session('other', source='cli')
        append(db, 'other', 'foreign')
        target = db.get_messages('other')[0]['id']
    elif kind == 'assistant':
        target = db.get_messages('child')[-1]['id']
    elif kind == 'missing':
        target = 999999
    elif kind == 'compacted':
        db.archive_and_compact('child', [{'role': 'user', 'content': 'new summary'}])
    else:
        db.rewind_to_message('child', target)
    before = db.get_active_message_ids('child')
    row_before = db.get_session(ref.session_id)
    with pytest.raises(RuntimeStoreError, match='invalid_params'):
        await mutate_session(authority, actor, ref,
            mutation(authority, ref.session_id, 'rewind', {'target_message_id': target}))
    assert db.get_active_message_ids('child') == before
    assert db.get_session(ref.session_id)['runtime_generation'] == row_before['runtime_generation']


@pytest.mark.asyncio
@pytest.mark.parametrize('foreign', ['principal', 'profile'])
async def test_child_metadata_preserves_owner_authorization(tmp_path, monkeypatch, foreign):
    from gateway.session_contract import SessionRef
    from gateway.session_mutations import mutate_session
    from hermes_state_runtime import RuntimeStoreError
    authority, actor, ref, _ = await local(tmp_path, monkeypatch)
    append(authority.db, ref.session_id, 'OLD_Q', 'OLD_A', 'TAIL_Q', 'TAIL_A')
    rotate(authority.db, ref.session_id)
    actor = replace(actor, **({'subject': 'intruder'} if foreign == 'principal' else {'profile_id': 'other'}))
    with pytest.raises(RuntimeStoreError, match='permission_denied|profile_mismatch'):
        await mutate_session(authority, actor, SessionRef('fixture', 'child'),
            mutation(authority, 'child', 'rename', {'title': 'stolen'}))
    assert authority.db.get_session('child')['title'] != 'stolen'


@pytest.mark.asyncio
async def test_stamped_secondary_route_uses_its_own_receipt(tmp_path, monkeypatch):
    from tests.gateway.test_session_authorities_multiplex import _reserve_homes, _runner
    from gateway import run
    from gateway.run_runtime import initialize_gateway_runtime
    from gateway.runtime_ownership import process_ownership
    from gateway.session_authorities import owner_scope
    from gateway.session_contract import Principal
    from gateway.session_local import create_local_session
    monkeypatch.setattr(run, '_load_gateway_config', lambda: {'platform_toolsets': {'cli': []}})
    root, homes = _reserve_homes(tmp_path, monkeypatch, names=('secondary',))
    process_ownership.reserve([home for _, home in homes])
    runner = _runner(root, homes)
    try:
        await initialize_gateway_runtime(runner)
        authority = runner.session_authorities.for_home(homes[1][1])
        db = authority.db
        actor = Principal('owner', authority.profile_id, frozenset({'session:create', 'session:read'}), 'sock')
        with owner_scope(authority):
            ref = create_local_session(authority, actor, dict(
                request_id='review', source='gui', cwd=str(tmp_path), model='frozen', toolsets=[]))
        live = authority.sessions[ref.session_id]
        assert live.route.startswith('agent:secondary:')
        append(db, ref.session_id, 'secondary question', 'secondary answer')
        db.end_session(ref.session_id, 'ws_orphan_reap')
        route = runner.session_store.get_or_create_session(live.source)
        assert route.session_id == ref.session_id
        assert runner.session_authorities.launch.db.get_session(ref.session_id) is None
        assert db.get_messages(ref.session_id)[0]['content'] == 'secondary question'
    finally:
        for authority in list(runner.session_authorities):
            authority.db.close()
        for _, home in homes:
            process_ownership.release(home)


@pytest.mark.asyncio
async def test_recovery_does_not_discard_a_stray_with_history(tmp_path, monkeypatch):
    from gateway.session_local_recovery import restore_local_session
    from hermes_state_runtime import RuntimeStoreError
    authority, _, ref, _ = await local(tmp_path, monkeypatch)
    live = authority.sessions.pop(ref.session_id)
    authority.db.create_session('stray', 'local', session_key=live.route,
        chat_id=live.source.chat_id, chat_type='dm', user_id=live.source.user_id)
    append(authority.db, 'stray', 'preserve this text')
    store = authority.runner.session_store
    with store._lock:
        store._entries[live.route].session_id = 'stray'
    with pytest.raises(RuntimeStoreError, match='admission_conflict'):
        restore_local_session(authority, ref.session_id)
    assert store._entries[live.route].session_id == 'stray'
    assert authority.db.get_messages('stray')[0]['content'] == 'preserve this text'
