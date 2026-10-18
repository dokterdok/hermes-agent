"""Probe (lane 4a review, LC-22 + the #134889 user report of 2026-10-18 07:01Z): control mutations and
resume of a compressed local conversation addressed by its TIP id.

Shape (a): an upgraded Desktop chat (numeric/timestamp id, NOT ``local-``), adopted by the native
owner, then compressed three times. Every row carries the owner id as ``chat_id``; the only receipt
is ``state_meta[POLICY_PREFIX + owner]`` whose ``entry.session_id`` is the tip and whose lineage holds
all four ids. Shape (b): a native ``local-`` conversation (tests/gateway/test_local_resume_compression_child).

Real SessionStore, SQLite, authority, adoption, compression publish and AuthorityConnection.dispatch;
every mutation runs on a cold authority (a restarted gateway), as the Desktop sidebar would after a restart.
"""
from types import SimpleNamespace

import pytest

from tests.gateway.test_local_resume_compression_child import _compress, _runner

OWNER = '20261015_111010_fe1164'
TIP = '20261016_170020_c7fd05'
CAPS = ['session:create', 'session:read', 'session:submit', 'session:control']


def _native(authority):
    from gateway.session_controls import AuthorityConnection
    return AuthorityConnection(authority, object(), {'provider': 'local', 'user_id': 'native',
        'profile_id': 'fixture', 'instance_id': authority.instance_id, 'capabilities': CAPS})


async def _authority(tmp_path, instance):
    from gateway.session_authority import initialize_session_authority
    return await initialize_session_authority(_runner(tmp_path), profile_id='fixture', instance_id=instance)


async def _legacy_compressed(tmp_path, monkeypatch):
    from gateway import run
    from gateway.session_contract import SessionRef
    from gateway.session_local_recovery import reopen_local_session
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(run, '_load_gateway_config', lambda: {'platform_toolsets': {'gui': [], 'cli': []}})
    first = await _authority(tmp_path, 'first')
    db = first.db
    db.create_session(OWNER, source='desktop', model='old-model', cwd=str(tmp_path))
    db.append_message(OWNER, role='user', content='OLD_Q')
    db.append_message(OWNER, role='assistant', content='OLD_A')
    db.end_session(OWNER, 'session_switch')
    adopted = await _native(first).dispatch({'id': 1, 'method': 'session.resume', 'params': {'session_id': OWNER}})
    assert adopted.get('result', {}).get('session_id') == OWNER, adopted
    ref = SessionRef('fixture', OWNER)
    reopen_local_session(first, ref)  # compression runs inside a turn, which reopens the adopted row
    live = first.sessions[OWNER]
    store = first.runner.session_store
    for parent, child in ((OWNER, '20261015_143220_1f28d2'), ('20261015_143220_1f28d2', '20261015_172238_c30208'),
                          ('20261015_172238_c30208', TIP)):
        store._record_gateway_session_peer(parent, live.route, live.source)
        _compress(db, parent, child, db.get_session(parent)['source'])
        store._record_gateway_session_peer(child, live.route, live.source)
    # A restarted owner resumes the conversation once: its receipt catches up to the tip (#140060).
    second = await _authority(tmp_path, 'second')
    caught = await _native(second).dispatch({'id': 2, 'method': 'session.resume', 'params': {'session_id': OWNER}})
    assert 'result' in caught, caught
    return tmp_path


def _assert_shape_a(db):
    from hermes_state_local import POLICY_PREFIX, local_receipt
    receipt = local_receipt(db, OWNER)
    assert receipt['entry']['session_id'] == TIP, receipt['entry']
    assert receipt['lineage'][0] == OWNER and TIP in receipt['lineage'] and len(receipt['lineage']) == 4
    assert db.get_session(TIP)['chat_id'] == OWNER and not OWNER.startswith('local-')
    with db._read_ctx() as conn:
        assert conn.execute('SELECT 1 FROM state_meta WHERE key=?', (POLICY_PREFIX + TIP,)).fetchone() is None


async def _mutate(connection, sid, request_id, operation, payload):
    row = connection.authority.db.get_session(sid)
    return await connection.dispatch({'id': 3, 'method': 'session.mutate', 'params': {
        'session_id': sid, 'request_id': request_id, 'expected_revision': row['runtime_revision'],
        'operation': operation, 'payload': payload}})


@pytest.mark.asyncio
@pytest.mark.parametrize('operation,payload,column,expected', [
    ('rename', {'title': 'Renamed'}, 'title', 'Renamed'),
    ('archive', {'archived': True}, 'archived', 1),
    ('sidebar', {'pinned': True}, 'pinned', 1),
])
async def test_shape_a_control_mutation_of_the_tip(tmp_path, monkeypatch, operation, payload, column, expected):
    await _legacy_compressed(tmp_path, monkeypatch)
    cold = await _authority(tmp_path, 'third')
    _assert_shape_a(cold.db)
    reply = await _mutate(_native(cold), TIP, f'{operation}-1', operation, payload)
    assert 'result' in reply, reply
    values = {sid: cold.db.get_session(sid).get(column) for sid in (OWNER, TIP)}
    assert expected in values.values(), values


@pytest.mark.asyncio
async def test_shape_a_resume_of_the_tip(tmp_path, monkeypatch):
    await _legacy_compressed(tmp_path, monkeypatch)
    cold = await _authority(tmp_path, 'third')
    _assert_shape_a(cold.db)
    reply = await _native(cold).dispatch({'id': 4, 'method': 'session.resume', 'params': {'session_id': TIP}})
    assert reply.get('result', {}).get('session_id') == OWNER, reply
    live = [m['content'] for m in reply['result']['messages'] if not m.get('archived')]
    assert live == ['retained', 'history'], reply['result']['messages']


@pytest.mark.asyncio
@pytest.mark.parametrize('operation,payload,column,expected', [
    ('sidebar', {'pinned': True}, 'pinned', 1),
])
async def test_shape_b_other_control_mutation_of_the_tip(tmp_path, monkeypatch, operation, payload, column, expected):
    from tests.gateway.test_local_resume_compression_child import _cold, _compressed
    from gateway.session_controls import AuthorityConnection
    compressed = await _compressed(tmp_path, monkeypatch)
    cold = await _cold(compressed)
    connection = AuthorityConnection(cold, object(), {
        'provider': 'local', 'user_id': 'owner', 'profile_id': 'fixture', 'instance_id': cold.instance_id,
        'capabilities': CAPS})
    reply = await _mutate(connection, 'child-2', f'{operation}-1', operation, payload)
    assert 'result' in reply, reply
    values = {sid: cold.db.get_session(sid).get(column) for sid in (compressed.owner, 'child-1', 'child-2')}
    assert expected in values.values(), values


@pytest.mark.asyncio
async def test_shape_a_another_principal_cannot_rename_the_tip(tmp_path, monkeypatch):
    from gateway.session_controls import AuthorityConnection
    await _legacy_compressed(tmp_path, monkeypatch)
    cold = await _authority(tmp_path, 'third')
    intruder = AuthorityConnection(cold, object(), {'provider': 'local', 'user_id': 'intruder',
        'profile_id': 'fixture', 'instance_id': cold.instance_id, 'capabilities': CAPS})
    reply = await _mutate(intruder, TIP, 'steal-1', 'rename', {'title': 'stolen'})
    assert reply.get('error', {}).get('message') in {'permission_denied', 'not_found'}, reply
    assert {cold.db.get_session(sid).get('title') for sid in (OWNER, TIP)} != {'stolen'}
    assert 'stolen' not in {cold.db.get_session(sid).get('title') for sid in (OWNER, TIP)}
