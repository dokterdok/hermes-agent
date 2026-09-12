"""Only explicit source grants and canonical installation owners enter passive setup."""
from types import SimpleNamespace

import pytest

from tests.gateway.test_canonical_passive_lifecycle import owners, native, create_source_room  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize('opted,owned', [(False, True), (True, False), (True, True)])
async def test_publisher_requires_optin_and_canonical_room_owner(owners, opted, owned):
    from gateway import hosted_room_links, hosted_room_peer
    from gateway.session_authorities import owner_scope
    from gateway.session_hosted_service import _OWNER
    from gateway.session_group_controls import dispatch_group_control
    from gateway.session_passive_replication import prepare_passive_publishers
    source, target, api = owners
    home_id, _ = create_source_room(source, target)
    invitation = await dispatch_group_control(target, 'groups.peer.invite', dict(room_id='room',
        member_id='peer', home_install_id=home_id, authority_gateway_id=home_id, authority_epoch=1,
        replication=opted, passive_only=opted))
    with owner_scope(source.authority):
        link = hosted_room_links.make_stored_link(room_id='room', member_id='peer',
            target_url='http://127.0.0.1:9876', target_profile='default', grant=invitation['grant'],
            catalog=hosted_room_peer.GatewayRoomCatalog.from_mapping(invitation['catalog']),
            cancellation_scope_id='scope', trace_id='trace')
        hosted_room_links.save_room_link(source.authority.db.db_path, link)
    if not owned:
        source.authority.db._execute_write(lambda conn: conn.execute('DELETE FROM state_meta WHERE key=?', (_OWNER + 'room',)))
    await prepare_passive_publishers(source.runner)
    publisher = source.authority.passive_publisher
    await prepare_passive_publishers(source.runner)
    assert source.authority.passive_publisher is publisher
    route = publisher._load_route(('room', 'peer'))
    if not opted:
        assert route is None
    else:
        assert route is not None
        source.runner.session_runtime_descriptor['state'] = 'ready'
        assert (publisher._checkpoint(route) is not None) is owned
    assert publisher._threads == []


@pytest.mark.asyncio
async def test_named_native_owner_cannot_enroll_or_borrow_root_publisher(owners):
    from gateway.session_authorities import owner_scope
    from gateway.session_authority import SessionAuthority
    from gateway.session_contract import Principal
    from gateway.session_hosted_service import CanonicalHostedRoomService
    from gateway.session_passive_replication import prepare_passive_publishers
    from hermes_state import SessionDB
    from hermes_state_runtime import RuntimeStoreError, begin_runtime_epoch
    source, _, _ = owners
    named = source.home / 'profiles' / 'named'
    named.mkdir(parents=True, mode=0o700)
    (named / 'config.yaml').write_text('{}')
    with SessionDB(named / 'state.db') as db:
        authority = SessionAuthority(source.runner, profile_id=str(named), instance_id='named', db=db,
            epoch=begin_runtime_epoch(db, instance_id='named'))
        source.runner.session_authorities.add(named, authority, name='named')
        with owner_scope(authority):
            authority.hosted_room_service = CanonicalHostedRoomService(authority, None)
        entry = SimpleNamespace(authority=authority, actor=Principal('native-owner', str(named),
            frozenset({'session:control', 'session:read'}), 'native'))
        for method, params, reason in (
            ('groups.replication.enroll', {'enrollment': {}}, 'installation_endpoint_required'),
            ('groups.replication.revoke', {'room_id': 'room', 'enrollment_id': 'one'}, 'installation_endpoint_required'),
            ('groups.replication.prepare', {'room_id': 'room'}, 'passive_source_requires_installation_owner')):
            with pytest.raises(RuntimeStoreError, match=reason):
                await native(entry, method, params)
        await prepare_passive_publishers(source.runner)
        assert getattr(authority, 'passive_publisher', None) is None
        assert source.authority.passive_publisher.db_path == source.authority.db.db_path
