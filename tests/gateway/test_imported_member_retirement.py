"""Remote imported member retirement must refuse absent Route provider without mutating identity."""
from types import SimpleNamespace

import pytest

from gateway import hosted_room_links as links, hosted_rooms as rooms
from gateway.hosted_room_execution_policy import execution_policy_mapping
from gateway.hosted_room_peer import GatewayRoomCatalog, catalog_mapping, issue_room_grant
from gateway.session_hosted_service import CanonicalHostedRoomService
from hermes_state import SessionDB
from hermes_state_runtime import RuntimeStoreError, begin_runtime_epoch


def test_missing_route_digest_provider_refuses_remote_retirement_without_mutation(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(rooms, 'local_authority_gateway_id', lambda: 'gateway-a')
    with SessionDB(tmp_path / 'state.db') as db:
        authority = SimpleNamespace(profile_id=str(tmp_path), db=db, instance_id='retirement-test', events={},
                                    epoch=begin_runtime_epoch(db, instance_id='retirement-test'))
        service = CanonicalHostedRoomService(authority, None)
        service.local_profiles = lambda: ('default', 'reviewer')
        service.runtime = SimpleNamespace(wakeup=lambda: None)
        imported = service.import_shipped_group_history(
            actor_subject='alice', room_id='room', name='Release', source_id='shipped-room',
            members=[{'source_member_id': name, 'name': name, 'handle': name, 'profile': name,
                      'remote_source': name == 'builder'} for name in ['default', 'reviewer', 'builder']],
            history=[], held_work=[])
        member_id = imported['room']['members'][2]['member_id']
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
        before = next(m for m in rooms.room_state(db.db_path, room_id='room')['members']
                      if m['member_id'] == member_id)
        with pytest.raises(RuntimeStoreError, match='peer_setup_unavailable'):
            service.resolve_shipped_group_member(
                actor_subject='alice', room_id='room', member_id=member_id, action='retire')
        after = next(m for m in rooms.room_state(db.db_path, room_id='room')['members']
                     if m['member_id'] == member_id)
        assert after == before
