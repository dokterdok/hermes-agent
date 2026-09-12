"""Native-owner recovery inspection uses retained state without activation or repair."""
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from tests.gateway.test_canonical_hosted_outputs import owner


def connection(authority, *, native=True, instance_id=None):
    from gateway.session_controls import AuthorityConnection
    return AuthorityConnection(authority, SimpleNamespace(), {
        'provider': 'local' if native else 'dashboard', 'user_id': 'alice',
        'profile_id': authority.profile_id, 'instance_id': instance_id or authority.instance_id,
        'capabilities': ['session:read', 'session:control', 'session:create', 'session:submit'],
        'native_bootstrap': native})


def dump(path):
    with sqlite3.connect(path) as conn:
        return list(conn.iterdump())


@pytest.mark.asyncio
async def test_native_preview_is_readonly_and_missing_work_never_authorizes_execution(tmp_path, monkeypatch):
    from gateway import hosted_rooms, hosted_room_driver as tasks, hosted_room_work_records as work
    from gateway.hosted_room_peer import issue_room_grant, decode_room_grant, gateway_room_grant_secret
    from gateway.hosted_room_replica_ingress import ingest_granted_page
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        native = connection(authority)
        target_id = hosted_rooms.local_authority_gateway_id()
        source = tmp_path / 'remote-source.db'
        shared = hosted_rooms.default_db_path()
        members = [dict(member_id='original', profile='default', handle='original'),
                   dict(member_id='target', profile='default', handle='target', target={
                       'kind': 'peer', 'installation_id': target_id, 'profile': 'default',
                       'peer_id': 'target', 'capability_digest': 'a' * 64})]
        hosted_rooms.create_room(source, room_id='saved-room', name='Saved group', members=members,
                                 authority_gateway_id='install:original')
        hosted_rooms.append_event(source, room_id='saved-room', event_id='event', kind='message.user',
            actor={'kind': 'user', 'id': 'source-owner'}, payload={'text': 'PRIVATE SOURCE TEXT'},
            authority_gateway_id='install:original', authority_epoch=1)
        secret = gateway_room_grant_secret()
        token = issue_room_grant(secret, grant_id='copy', room_id='saved-room', home_install_id='install:original',
            authority_gateway_id='install:original', authority_epoch=1, member_id='target',
            target_install_id=target_id, target_profile='default', permissions=('status', 'replicate', 'work_records'))
        claims = decode_room_grant(secret, token, permission='replicate')
        hosted_rooms.reserve_peer_room(shared, claims=claims, expires_at=claims['status_expires_at'])
        ingest_granted_page(shared, token=token, secret=secret, target_install_id=target_id, target_profile='default',
            room_id='saved-room', room_name='Saved group', members=members, page=hosted_rooms.read_events(source, room_id='saved-room'))
        before, local = dump(shared), dump(authority.db.db_path)
        request = {'id': 1, 'method': 'groups.recovery.prepare', 'params': {'room_id': 'saved-room'}}
        result = await native.dispatch(request)
        assert 'error' not in result, result
        preview = result['result']
        assert preview['room_id'] == 'saved-room'
        assert preview['source_authority'] == {'gateway_id': 'install:original', 'epoch': 1}
        assert preview['accepted_tail'] == 'unverified' and preview['execution_authorized'] is False
        assert preview['reconciliation_required'] is True
        assert 'work_records_unavailable' in preview['blockers']
        assert 'PRIVATE SOURCE TEXT' not in json.dumps(preview)
        assert dump(shared) == before and dump(authority.db.db_path) == local
        remote = connection(authority, native=False)
        assert (await remote.dispatch(request))['error']['message'] == 'permission_denied'
        stale = connection(authority, instance_id='old-instance')
        assert (await stale.dispatch(request))['error']['message'] == 'permission_denied'
        forged = {**request, 'params': {**request['params'], 'native_owner': True}}
        assert 'error' in await remote.dispatch(forged)
        assert dump(shared) == before
        tasks.admit_task(source, tasks.TaskIdentity('saved-room', 'known-task', 'thread', 'turn'),
            payload={'target_profile': 'default', 'target_member_id': 'original',
                     'source_event_seq': 1, 'prompt': 'PRIVATE WORK INPUT'}, clock=lambda: 10)
        record = work.capture(source, room_id='saved-room', local_gateway_id='install:original')
        work.ingest(shared, record=record, token=token, secret=secret, target_install_id=target_id, target_profile='default')
        before = dump(shared)
        current = (await native.dispatch(request))['result']
        assert current['snapshot_id'] != preview['snapshot_id']
        assert current['execution_authorized'] is False and current['accepted_tail'] == 'unverified'
        assert current['work_records']['tasks'][0]['phase'] == 'queued'
        assert 'PRIVATE WORK INPUT' not in json.dumps(current)
        assert dump(shared) == before and dump(authority.db.db_path) == local
        assert not authority.sessions and not authority.db._read_all('SELECT * FROM session_admissions')


@pytest.mark.asyncio
async def test_missing_preview_does_not_create_store_or_recovery_capability(tmp_path, monkeypatch):
    from gateway import hosted_rooms
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    async with owner(tmp_path, monkeypatch) as (authority, service, runner):
        native = connection(authority)
        missing = tmp_path / 'never-created.db'
        monkeypatch.setattr(hosted_rooms, 'default_db_path', lambda: missing)
        result = await native.dispatch({'id': 1, 'method': 'groups.recovery.prepare', 'params': {'room_id': 'missing'}})
        assert 'error' in result and not missing.exists()
        for method in ('groups.promote', 'groups.recovery.commit', 'groups.paused.commit', 'groups.recovery.fence'):
            assert 'error' in await native.dispatch({'id': 2, 'method': method, 'params': {'room_id': 'room'}})
        for room_id in (' room', '', 3, None):
            bad = await native.dispatch({'id': 3, 'method': 'groups.recovery.prepare', 'params': {'room_id': room_id}})
            assert bad['error']['message'] == 'invalid_params'
