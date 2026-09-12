"""Existing classic custody through real canonical access, without execution."""
import base64
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def exported(tmp_path, monkeypatch):
    from gateway import run
    from gateway.config import GatewayConfig
    from gateway.hosted_room_artifacts_classic import ClassicExports
    from gateway.hosted_rooms import local_authority_gateway_id
    from gateway.session import SessionStore
    from gateway.session_authority import initialize_session_authority
    from gateway.session_controls import AuthorityConnection
    from gateway.session_local import create_local_session
    from hermes_state import SessionDB

    home = tmp_path.resolve()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(Path, 'home', lambda: home)
    monkeypatch.setattr(run, '_load_gateway_config', lambda: {'platform_toolsets': {'gui': []}})
    store = SessionStore(home / 'sessions', GatewayConfig())
    store._db.close()
    store._db = SessionDB(home / 'state.db')
    runner = SimpleNamespace(session_store=store, _session_db=store._db, adapters={}, _draining=False)
    authority = await initialize_session_authority(runner, profile_id=str(home), instance_id='read-test')
    connection = AuthorityConnection(authority, object(), {'user_id': 'owner', 'provider': 'local',
        'profile_id': str(home), 'instance_id': 'read-test', 'native_bootstrap': True})
    ref = create_local_session(authority, connection.actor, {'request_id': 'original', 'source': 'gui',
        'cwd': str(home), 'model': 'fixture', 'toolsets': []})
    installation = local_authority_gateway_id()
    import time
    exports = ClassicExports(home)
    row, _ = exports.admit(ref.session_id, {'request_id': 'old-export', 'group_id': 'old-room',
        'thread_id': 'thread', 'recipients': [{'installation': installation, 'profile': 'default'}],
        'issued_at': time.time()}, 'old request')
    data = b'already published original bytes\n'
    item = exports.outbox.put_bytes(scope=exports.scope(row), data=data, source_name='report.txt')
    exports.settle(row['export_id'], 'published', True)
    assert Path(authority.db.db_path) == exports.outbox.db_path
    params = dict(session_id=ref.session_id, installation=installation, group_id='old-room',
                  export_id=row['export_id'], artifact_id=item['artifact_id'], generation=row['generation'])
    fixture = SimpleNamespace(home=home, db=authority.db, authority=authority, connection=connection,
        ref=ref, exports=exports, row=row, item=item, params=params, data=data)
    try:
        yield fixture
    finally:
        await connection.close()
        store._db.close()


async def read(fixture, **changes):
    return await fixture.connection.dispatch({'id': 1, 'method': 'session.export.read',
                                              'params': {**fixture.params, **changes}})


def dump(fixture):
    with fixture.db._read_ctx() as conn:
        return tuple(conn.iterdump())


@pytest.mark.asyncio
async def test_published_read_is_non_creating_and_exact(exported, monkeypatch):
    from gateway.hosted_room_artifacts import RoomArtifactOutbox
    from gateway.hosted_room_artifacts_classic import ClassicExports
    before = dump(exported)
    def forbidden(*args, **kwargs):
        pytest.fail('a read must not construct or initialize custody')
    monkeypatch.setattr(ClassicExports, '__init__', forbidden)
    monkeypatch.setattr(RoomArtifactOutbox, '__init__', forbidden)
    monkeypatch.setattr(RoomArtifactOutbox, '_initialize', forbidden)
    result = await read(exported)
    assert 'result' in result, result
    value = result['result']
    assert value['session_id'] == exported.ref.session_id
    assert value['item'] == exported.item
    assert value['export_id'] == exported.row['export_id']
    assert value['generation'] == exported.row['generation']
    assert base64.b64decode(value['content_base64'], validate=True) == exported.data
    assert 'text' not in value and 'path' not in json.dumps(value)
    assert dump(exported) == before
    assert not exported.db._read_all('SELECT * FROM session_admissions')


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [
    {'installation': 'install:other'}, {'group_id': 'another-room'}, {'generation': 2},
    {'generation': True}, {'artifact_id': 'rart_' + '0' * 32}, {'session_id': 'not-bound'},
    {'profile': 'named'}, {'path': '/not/a/read/argument'}, {'request_id': 'old-export'},
])
async def test_exact_selectors_do_not_fall_back(exported, change):
    before = dump(exported)
    assert 'error' in await read(exported, **change)
    assert dump(exported) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['foreign', 'missing_read', 'closed', 'cold', 'named'])
async def test_existing_read_authority_is_required(exported, kind):
    connection, authority = exported.connection, exported.authority
    if kind == 'foreign':
        connection.actor = replace(connection.actor, subject='other')
    elif kind == 'missing_read':
        connection.actor = replace(connection.actor, capabilities=frozenset())
    elif kind == 'closed':
        await connection.close()
    elif kind == 'cold':
        authority.sessions.clear()
    else:
        authority.profile_id = str(exported.home / 'profiles' / 'named')
        connection.actor = replace(connection.actor, profile_id=authority.profile_id)
    before = dump(exported)
    assert 'error' in await read(exported)
    assert dump(exported) == before


@pytest.mark.asyncio
async def test_an_unrelated_authorized_session_cannot_read_the_export(exported):
    from gateway.session_local import create_local_session
    other = create_local_session(exported.authority, exported.connection.actor,
        {'request_id': 'other', 'source': 'gui', 'cwd': str(exported.home), 'model': 'fixture', 'toolsets': []})
    assert 'error' in await read(exported, session_id=other.session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['retire', 'epoch', 'binding', 'delete_session', 'close_transport', 'read_scope'])
async def test_fresh_checks_after_byte_read_refuse_changes(exported, monkeypatch, change):
    from gateway import session_classic_exports as reader
    original = reader._read_bytes
    calls = []
    def changed(*args):
        calls.append(True)
        data = original(*args)
        if change == 'retire':
            exported.db._execute_write(lambda conn: conn.execute(
                "UPDATE classic_output_exports SET state='retired' WHERE export_id=?", (exported.row['export_id'],)))
        elif change == 'epoch':
            from hermes_state_runtime import begin_runtime_epoch
            begin_runtime_epoch(exported.db, instance_id='different-owner')
        elif change == 'binding':
            exported.db._execute_write(lambda conn: conn.execute(
                "UPDATE classic_output_exports SET session_key='unrelated' WHERE export_id=?", (exported.row['export_id'],)))
        elif change == 'delete_session':
            exported.db._execute_write(lambda conn: conn.execute(
                'DELETE FROM sessions WHERE id=?', (exported.ref.session_id,)))
        elif change == 'close_transport':
            exported.authority.events.pop(exported.connection.actor.transport_id)
        else:
            exported.connection.actor = replace(exported.connection.actor, capabilities=frozenset())
        return data
    monkeypatch.setattr(reader, '_read_bytes', changed)
    assert 'error' in await read(exported)
    assert calls == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['missing_schema', 'missing_bytes', 'changed_bytes', 'symlink', 'retirement'])
async def test_unavailable_custody_is_not_repaired(exported, kind):
    row = exported.db._read_one('SELECT blob_name FROM hosted_room_output_artifacts')
    path = exported.home / 'hosted-room-artifact-outbox' / 'blobs' / row['blob_name']
    if kind == 'missing_schema':
        exported.db._execute_write(lambda conn: conn.execute('DROP TABLE classic_retired_groups'))
    elif kind == 'retirement':
        exported.db._execute_write(lambda conn: conn.execute(
            'INSERT INTO classic_retired_groups VALUES (?,?)', (str(exported.home), 'old-room')))
    elif kind == 'symlink':
        other = exported.home / 'outside.txt'
        other.write_bytes(exported.data)
        path.unlink()
        path.symlink_to(other)
    elif kind == 'changed_bytes':
        path.write_bytes(b'x' * len(exported.data))
    else:
        path.unlink()
    before = dump(exported)
    assert 'error' in await read(exported)
    assert dump(exported) == before


@pytest.mark.asyncio
async def test_missing_custody_database_is_not_created(exported):
    # The authority remains bound to the existing connection; no missing-file fallback.
    original = exported.authority.db.db_path
    absent = exported.home / 'absent.db'
    exported.authority.db.db_path = str(absent)
    try:
        assert 'error' in await read(exported)
        assert not absent.exists()
    finally:
        exported.authority.db.db_path = original


@pytest.mark.asyncio
async def test_exact_compression_lineage_is_supported_but_ambiguity_is_not(exported):
    db, sid = exported.db, exported.ref.session_id
    assert db.try_acquire_compression_lock(sid, 'metadata-fixture')
    try:
        db.publish_compression_child(parent_session_id=sid, child_session_id='exact-child',
            source='gui', messages=[{'role': 'user', 'content': 'retained context'}],
            compression_lock_holder='metadata-fixture')
    finally:
        db.release_compression_lock(sid, 'metadata-fixture')
    before = dump(exported)
    assert 'result' in await read(exported)
    assert dump(exported) == before
    db.create_session('competing-child', source='gui', parent_session_id=sid)
    assert 'error' in await read(exported)


@pytest.mark.asyncio
async def test_deleted_or_unbound_original_session_never_gets_adopted(exported, monkeypatch):
    from hermes_state_local import POLICY_PREFIX
    def forbidden(*args, **kwargs):
        pytest.fail('read cannot adopt or restore a session')
    monkeypatch.setattr('gateway.session_local_migration.adopt_legacy_session', forbidden)
    monkeypatch.setattr('gateway.session_local_recovery.restore_local_session', forbidden)
    exported.db._execute_write(lambda conn: conn.execute('DELETE FROM state_meta WHERE key=?',
        (POLICY_PREFIX + exported.ref.session_id,)))
    before = dump(exported)
    assert 'error' in await read(exported)
    assert dump(exported) == before


def test_readonly_opener_does_not_create_missing_storage(tmp_path):
    from gateway.session_classic_exports import _readonly
    missing = tmp_path / 'missing' / 'state.db'
    import sqlite3
    with pytest.raises(sqlite3.OperationalError):
        with _readonly(missing):
            pytest.fail('missing store was created')
    assert not missing.parent.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('value', [[], ['bad'], 'bad', 1])
async def test_non_object_rpc_is_refused(exported, value):
    reply = await exported.connection.dispatch({'id': 1, 'method': 'session.export.read', 'params': value})
    assert reply['error']['message'] == 'invalid_params'
