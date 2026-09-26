"""Remote imported member retirement refuses a missing Route digest and retires when it is present."""
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from gateway import hosted_room_links as links, hosted_rooms as rooms
from gateway.hosted_room_execution_policy import execution_policy_mapping
from gateway.hosted_room_peer import GatewayRoomCatalog, catalog_mapping, issue_room_grant
from gateway.session_hosted_service import CanonicalHostedRoomService
from hermes_state import SessionDB
from hermes_state_runtime import RuntimeStoreError, begin_runtime_epoch

_IDENTITY = ('member_id', 'profile', 'handle', 'display_name', 'source')


class _PeerBody:
    def __init__(self, payload=b'{"revoked":true}'):
        self._buf = payload
        self.headers = {}

    def read(self, n):
        chunk = self._buf[:n]
        self._buf = self._buf[n:]
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _member(db_path, member_id, *, include_disbanded=False):
    room = rooms.room_state(db_path, room_id='room', include_disbanded=include_disbanded)
    return next(m for m in room['members'] if m['member_id'] == member_id), room


@contextmanager
def _open_imported_room(tmp_path, monkeypatch, *remote_profiles):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(rooms, 'local_authority_gateway_id', lambda: 'gateway-a')
    names = ['default', 'reviewer', *remote_profiles]
    with SessionDB(tmp_path / 'state.db') as db:
        authority = SimpleNamespace(profile_id=str(tmp_path), db=db, instance_id='retirement-test', events={},
                                    epoch=begin_runtime_epoch(db, instance_id='retirement-test'))
        service = CanonicalHostedRoomService(authority, None)
        service.local_profiles = lambda: ('default', 'reviewer')
        service.runtime = SimpleNamespace(wakeup=lambda: None)
        imported = service.import_shipped_group_history(
            actor_subject='alice', room_id='room', name='Release', source_id='shipped-room',
            members=[{'source_member_id': name, 'name': name, 'handle': name, 'profile': name,
                      'remote_source': name in remote_profiles} for name in names],
            history=[], held_work=[])
        yield service, db, imported


def _save_builder_link(db, member_id):
    catalog = catalog_mapping(installation_id='peer-install', target_profile='builder', persistent_process=True,
                              execution_policy=execution_policy_mapping(target_profile='builder', config={}))
    grant = issue_room_grant(
        b'x' * 32, grant_id='first', room_id='room', home_install_id='gateway-a',
        authority_gateway_id='gateway-a', authority_epoch=1, member_id=member_id,
        target_install_id='peer-install', target_profile='builder',
        execution_policy_digest=catalog['execution_policy']['policy_digest'])
    link = links.make_stored_link(
        room_id='room', member_id=member_id, target_url='https://original.example', target_profile='builder',
        grant=grant, catalog=GatewayRoomCatalog.from_mapping(catalog), cancellation_scope_id='cancel', trace_id='trace')
    links.save_room_link(db.db_path, link)
    return grant, link


def test_missing_route_digest_provider_refuses_remote_retirement_without_mutation(tmp_path, monkeypatch):
    monkeypatch.delattr(links, 'route_security_digest', raising=False)
    with _open_imported_room(tmp_path, monkeypatch, 'builder') as (service, db, imported):
        member_id = next(m['member_id'] for m in imported['room']['members'] if m['profile'] == 'builder')
        _save_builder_link(db, member_id)
        before, _room = _member(db.db_path, member_id)
        with pytest.raises(RuntimeStoreError, match='peer_setup_unavailable'):
            service.resolve_shipped_group_member(
                actor_subject='alice', room_id='room', member_id=member_id, action='retire')
        after, room = _member(db.db_path, member_id)
        assert after == before
        assert 'disbanded_at' not in room


def test_disbanded_room_refuses_remote_retirement_without_mutation(tmp_path, monkeypatch):
    with _open_imported_room(tmp_path, monkeypatch, 'builder') as (service, db, imported):
        member_id = next(m['member_id'] for m in imported['room']['members'] if m['profile'] == 'builder')
        _save_builder_link(db, member_id)
        before, room = _member(db.db_path, member_id)
        rooms.disband_room(
            db.db_path, room_id='room', expected_gateway_id=room['authority_gateway_id'],
            expected_epoch=room['authority_epoch'])
        with pytest.raises(RuntimeStoreError, match='room_unavailable'):
            service.resolve_shipped_group_member(
                actor_subject='alice', room_id='room', member_id=member_id, action='retire')
        after, disbanded = _member(db.db_path, member_id, include_disbanded=True)
        assert after == before
        assert after['membership'] == {'state': 'active'}
        assert disbanded['disbanded_at'] > 0


def test_remote_retirement_succeeds_with_route_security_digest(tmp_path, monkeypatch):
    with _open_imported_room(tmp_path, monkeypatch, 'builder', 'outsider') as (service, db, imported):
        builder_id = next(m['member_id'] for m in imported['room']['members'] if m['profile'] == 'builder')
        outsider_id = next(m['member_id'] for m in imported['room']['members'] if m['profile'] == 'outsider')
        grant, link = _save_builder_link(db, builder_id)
        before, room = _member(db.db_path, builder_id)
        outsiders_before, _room = _member(db.db_path, outsider_id)
        locals_before = {
            m['member_id']: m for m in room['members'] if m['profile'] in {'default', 'reviewer'}}
        if not callable(getattr(links, 'route_security_digest', None)):
            with pytest.raises(RuntimeStoreError, match='peer_setup_unavailable'):
                service.resolve_shipped_group_member(
                    actor_subject='alice', room_id='room', member_id=builder_id, action='retire')
            after, _room = _member(db.db_path, builder_id)
            assert after == before
            pytest.fail('route_security_digest is absent; remote retirement refused without mutation')
        record = link.as_record()
        digest = links.route_security_digest(record)
        health = dict(record, status='unavailable', updated_at=record['updated_at'] + 5)
        assert links.route_security_digest(health) == digest
        assert links.route_security_digest(dict(record, grant=record['grant'] + 'x')) != digest
        captured = []

        def open_credentialed_url(request, timeout=30, **_kwargs):
            captured.append(request)
            return _PeerBody()

        monkeypatch.setattr('hermes_cli.urllib_security.open_credentialed_url', open_credentialed_url)
        result = service.resolve_shipped_group_member(
            actor_subject='alice', room_id='room', member_id=builder_id, action='retire')
        after, room = _member(db.db_path, builder_id)
        assert result['action'] == 'retire' and result['changed'] is True
        assert result['member']['membership'] == {'state': 'former'}
        assert {key: after[key] for key in _IDENTITY} == {key: before[key] for key in _IDENTITY}
        assert after['membership'] == {'state': 'former'}
        assert after['availability'] == {'state': 'retired', 'reason': 'former_member'}
        assert 'target' not in after
        assert 'disbanded_at' not in room
        assert {m['member_id']: m for m in room['members'] if m['profile'] in {'default', 'reviewer'}} == locals_before
        stored = next(row for row in rooms.list_room_link_records(db.db_path) if row['member_id'] == builder_id)
        assert stored['status'] == 'needs_reauthorization'
        assert stored['grant'] == grant
        assert len(captured) == 1
        request = captured[0]
        assert request.full_url == 'https://original.example/p/builder/v1/room-members/grants/revoke-exact'
        assert request.get_method() == 'POST'
        assert request.get_header('Authorization') == f'HermesRoom {grant}'
        assert json.loads(request.data) == {}
        rooms.disband_room(
            db.db_path, room_id='room', expected_gateway_id=room['authority_gateway_id'],
            expected_epoch=room['authority_epoch'])
        with pytest.raises(RuntimeStoreError, match='room_unavailable'):
            service.resolve_shipped_group_member(
                actor_subject='alice', room_id='room', member_id=outsider_id, action='retire')
        outsider_after, disbanded = _member(db.db_path, outsider_id, include_disbanded=True)
        builder_after, _room = _member(db.db_path, builder_id, include_disbanded=True)
        assert outsider_after == outsiders_before
        assert outsider_after['membership'] == {'state': 'active'}
        assert builder_after['membership'] == {'state': 'former'}
        assert disbanded['disbanded_at'] > 0
