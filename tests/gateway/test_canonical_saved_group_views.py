"""Saved-copy discovery stays native-owner scoped and never resumes group work."""
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from tests.gateway.test_canonical_hosted_outputs import owner
from tests.gateway.test_canonical_inert_recovery import connection, dump


def save_copy(tmp_path, monkeypatch, *, room_id, name, received_at):
    from gateway import hosted_rooms as rooms, hosted_room_replicas as replicas
    from gateway.hosted_room_peer import issue_room_grant, decode_room_grant, gateway_room_grant_secret
    from gateway.hosted_room_replica_ingress import ingest_granted_page
    target = rooms.local_authority_gateway_id()
    source = tmp_path / (room_id + '-source.db')
    members = [dict(member_id='original', profile='default', handle='original'),
               dict(member_id='target', profile='default', handle='target', target={
                   'kind': 'peer', 'installation_id': target, 'profile': 'default',
                   'peer_id': 'target', 'capability_digest': 'a' * 64})]
    rooms.create_room(source, room_id=room_id, name=name, members=members,
                      authority_gateway_id='install:original')
    rooms.append_event(source, room_id=room_id, event_id='shared-input', kind='message.user',
        actor={'kind': 'user', 'id': 'source-owner'}, payload={'text': 'SHARED MESSAGE CONTENT'},
        authority_gateway_id='install:original', authority_epoch=1)
    secret = gateway_room_grant_secret()
    token = issue_room_grant(secret, grant_id='copy-' + room_id, room_id=room_id,
        home_install_id='install:original', authority_gateway_id='install:original', authority_epoch=1,
        member_id='target', target_install_id=target, target_profile='default',
        permissions=('status', 'replicate', 'work_records'))
    claims = decode_room_grant(secret, token, permission='replicate')
    shared = rooms.default_db_path()
    rooms.reserve_peer_room(shared, claims=claims, expires_at=claims['status_expires_at'])
    with monkeypatch.context() as clock:
        clock.setattr(replicas, 'time', SimpleNamespace(time=lambda: received_at))
        ingest_granted_page(shared, token=token, secret=secret, target_install_id=target,
            target_profile='default', room_id=room_id, room_name=name, members=members,
            page=rooms.read_events(source, room_id=room_id))
    return shared, token


@pytest.mark.asyncio
async def test_saved_copy_list_is_paginated_metadata_only_and_nonexecuting(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    async with owner(tmp_path, monkeypatch) as (authority, _, _):
        native = connection(authority)
        shared, token = save_copy(tmp_path, monkeypatch, room_id='older', name='Older group', received_at=10)
        save_copy(tmp_path, monkeypatch, room_id='newer', name='Caf\u00e9 planning', received_at=20)
        before, local = dump(shared), dump(authority.db.db_path)
        request = {'id': 1, 'method': 'groups.recovery.list', 'params': {'limit': 1}}
        first = await native.dispatch(request)
        assert 'error' not in first, first
        page = first['result']
        assert page['object'] == 'hermes.group_recovery.copies'
        assert page['execution_authorized'] is False and page['accepted_tail'] == 'unverified'
        assert [row['room_id'] for row in page['copies']] == ['newer']
        assert page['copies'][0]['name'] == 'Caf\u00e9 planning'
        assert page['next_room_id'] == 'newer'
        second = await native.dispatch({**request, 'params': {'limit': 1, 'after_room_id': page['next_room_id']}})
        assert [row['room_id'] for row in second['result']['copies']] == ['older']
        assert second['result']['next_room_id'] is None
        assert 'SHARED MESSAGE CONTENT' not in json.dumps(page) and token not in json.dumps(page)
        preview = await native.dispatch({'id': 2, 'method': 'groups.recovery.prepare', 'params': {'room_id': 'newer'}})
        assert preview['result']['room_id'] == 'newer'
        assert preview['result']['target_gateway_id'] == page['target_gateway_id']
        assert preview['result']['execution_authorized'] is False
        assert dump(shared) == before and dump(authority.db.db_path) == local
        assert not authority.sessions and not authority.db._read_all('SELECT * FROM session_admissions')


@pytest.mark.asyncio
async def test_saved_copy_list_keeps_owner_and_strict_parameter_boundaries(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    async with owner(tmp_path, monkeypatch) as (authority, _, _):
        shared, _ = save_copy(tmp_path, monkeypatch, room_id='saved', name='Saved group', received_at=10)
        before = dump(shared)
        request = {'id': 1, 'method': 'groups.recovery.list', 'params': {}}
        for remote in (connection(authority, native=False), connection(authority, instance_id='old')):
            result = await remote.dispatch(request)
            assert result['error']['message'] == 'permission_denied'
        native = connection(authority)
        for params in ({'limit': True}, {'limit': 0}, {'limit': 21}, {'offset': 1},
                       {'after_room_id': 1}, {'after_room_id': ' saved'},
                       {'native_owner': True}, {'path': str(shared)}):
            result = await native.dispatch({**request, 'params': params})
            assert result['error']['message'] == 'invalid_params', result
        assert dump(shared) == before


@pytest.mark.asyncio
async def test_missing_saved_copy_store_is_empty_without_creating_it(tmp_path, monkeypatch):
    from gateway import hosted_rooms
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    async with owner(tmp_path, monkeypatch) as (authority, _, _):
        native = connection(authority)
        missing = tmp_path / 'not-created.db'
        monkeypatch.setattr(hosted_rooms, 'default_db_path', lambda: missing)
        result = await native.dispatch({'id': 1, 'method': 'groups.recovery.list', 'params': {}})
        assert 'error' not in result, result
        assert result['result']['copies'] == [] and result['result']['next_room_id'] is None
        assert result['result']['execution_authorized'] is False
        assert not missing.exists() and not list(tmp_path.glob('not-created.db*'))


@pytest.mark.asyncio
async def test_copy_updates_do_not_move_the_pagination_cursor(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    async with owner(tmp_path, monkeypatch) as (authority, _, _):
        native = connection(authority)
        shared, _ = save_copy(tmp_path, monkeypatch, room_id='a-room', name='Planning', received_at=10)
        save_copy(tmp_path, monkeypatch, room_id='b-room', name='Review', received_at=20)
        request = {'id': 1, 'method': 'groups.recovery.list', 'params': {'limit': 1}}
        first = (await native.dispatch(request))['result']
        assert first['copies'][0]['room_id'] == 'a-room'
        with sqlite3.connect(shared) as conn:
            conn.execute("UPDATE hosted_room_replicas SET updated_at=30 WHERE room_id='a-room'")
        before = dump(shared)
        page = (await native.dispatch({**request, 'params': {
            'limit': 1, 'after_room_id': first['next_room_id']}}))['result']
        assert [item['room_id'] for item in page['copies']] == ['b-room']
        assert page['next_room_id'] is None and dump(shared) == before


@pytest.mark.asyncio
async def test_saved_copy_disclosure_rechecks_native_transport_after_read(tmp_path, monkeypatch):
    from gateway import hosted_room_saved_views
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    async with owner(tmp_path, monkeypatch) as (authority, _, _):
        native = connection(authority)
        shared, _ = save_copy(tmp_path, monkeypatch, room_id='saved', name='Planning', received_at=10)
        before = dump(shared)
        original = hosted_room_saved_views.list_saved_copies
        def read_then_detach(*args, **kwargs):
            result = original(*args, **kwargs)
            authority._native_legacy_transports.pop(native.actor.transport_id)
            return result
        monkeypatch.setattr(hosted_room_saved_views, 'list_saved_copies', read_then_detach)
        result = await native.dispatch({'id': 1, 'method': 'groups.recovery.list', 'params': {}})
        assert result['error']['message'] == 'permission_denied'
        assert 'Planning' not in json.dumps(result) and dump(shared) == before


@pytest.mark.asyncio
async def test_damaged_copy_header_is_unavailable_not_an_empty_store(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    async with owner(tmp_path, monkeypatch) as (authority, _, _):
        native = connection(authority)
        shared, _ = save_copy(tmp_path, monkeypatch, room_id='saved', name='Planning', received_at=10)
        with sqlite3.connect(shared) as conn:
            conn.execute("UPDATE hosted_room_replicas SET updated_at='invalid-time' WHERE room_id='saved'")
        before = dump(shared)
        result = await native.dispatch({'id': 1, 'method': 'groups.recovery.list', 'params': {}})
        assert result['error']['message'] == 'recovery_evidence_unavailable'
        assert 'result' not in result and dump(shared) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('stored_name', ['x' * 100000, 'x\x00' + 'y' * 100000])
async def test_oversized_stored_metadata_is_not_loaded_into_response_projection(tmp_path, monkeypatch, stored_name):
    from gateway import hosted_room_saved_views
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    async with owner(tmp_path, monkeypatch) as (authority, _, _):
        native = connection(authority)
        shared, _ = save_copy(tmp_path, monkeypatch, room_id='saved', name='Planning', received_at=10)
        with sqlite3.connect(shared) as conn:
            conn.execute("UPDATE hosted_room_replicas SET name=? WHERE room_id='saved'", (stored_name,))
        seen = []
        original = hosted_room_saved_views._summary
        def project(row):
            seen.append(row['name'])
            return original(row)
        monkeypatch.setattr(hosted_room_saved_views, '_summary', project)
        result = await native.dispatch({'id': 1, 'method': 'groups.recovery.list', 'params': {}})
        assert seen == [None]
        assert result['error']['message'] == 'recovery_evidence_unavailable'


@pytest.mark.asyncio
async def test_valid_multibyte_copy_names_keep_the_source_character_limit(tmp_path, monkeypatch):
    from gateway.hosted_rooms import MAX_ROOM_NAME_CHARS
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    async with owner(tmp_path, monkeypatch) as (authority, _, _):
        native = connection(authority)
        name = '\U0001f4c1' * MAX_ROOM_NAME_CHARS
        save_copy(tmp_path, monkeypatch, room_id='saved', name=name, received_at=10)
        result = await native.dispatch({'id': 1, 'method': 'groups.recovery.list', 'params': {}})
        assert result['result']['copies'][0]['name'] == name


@pytest.mark.asyncio
async def test_recorded_copy_flags_never_claim_recovery_readiness(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    async with owner(tmp_path, monkeypatch) as (authority, _, _):
        native = connection(authority)
        shared, _ = save_copy(tmp_path, monkeypatch, room_id='saved', name='Planning', received_at=10)
        with sqlite3.connect(shared) as conn:
            conn.execute("UPDATE hosted_room_replicas SET quarantine_reason='stored warning',disbanded_at=20 WHERE room_id='saved'")
        before = dump(shared)
        result = (await native.dispatch({'id': 1, 'method': 'groups.recovery.list', 'params': {}}))['result']
        assert result['copies'][0]['copy_status'] == 'needs_review'
        assert result['copies'][0]['group_ended'] is True
        assert result['execution_authorized'] is False and result['accepted_tail'] == 'unverified'
        assert dump(shared) == before
