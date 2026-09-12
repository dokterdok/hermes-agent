"""Canonical passive startup/enrollment with real stores and local HTTP, no inference."""
import asyncio
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


@pytest.fixture
def owners(tmp_path, monkeypatch):
    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter
    from gateway.session_authorities import SessionAuthorities, owner_scope
    from gateway.session_authority import SessionAuthority
    from gateway.session_contract import Principal
    from gateway.session_hosted_service import CanonicalHostedRoomService
    from hermes_cli import install_identity
    from hermes_constants import get_hermes_home
    from hermes_state import SessionDB
    from hermes_state_runtime import begin_runtime_epoch

    source, target = tmp_path / 'source', tmp_path / 'target'
    source.mkdir(mode=0o700)
    target.mkdir(mode=0o700)
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(target))
    monkeypatch.setattr(install_identity, 'get_default_hermes_root', get_hermes_home)
    result = []
    with ExitStack() as stack:
        for home in (source, target):
            (home / 'config.yaml').write_text('{}')
            db = stack.enter_context(SessionDB(home / 'state.db'))
            runner = SimpleNamespace(config=GatewayConfig(multiplex_profiles=True), adapters={},
                _profile_adapters={}, _running=True, _draining=False,
                session_runtime_descriptor={'state': 'starting'})
            authority = SessionAuthority(runner, profile_id=str(home), db=db, instance_id=home.name,
                                         epoch=begin_runtime_epoch(db, instance_id=home.name))
            runner.session_authority = authority
            runner.session_authorities = SessionAuthorities(home)
            runner.session_authorities.add(home, authority, name='default')
            with owner_scope(authority):
                authority.hosted_room_service = CanonicalHostedRoomService(authority, None)
            actor = Principal('native-owner', str(home), frozenset({'session:read', 'session:control'}), 'native')
            result.append(SimpleNamespace(authority=authority, actor=actor, runner=runner, home=home))
        with owner_scope(result[1].authority):
            api = APIServerAdapter(PlatformConfig(enabled=True, extra={'key': 'passive-test-only-api-key'}))
        api.gateway_runner = result[1].runner
        result[1].runner.adapters[Platform.API_SERVER] = api
        try:
            yield result[0], result[1], api
        finally:
            for entry in result:
                publisher = getattr(entry.authority, 'passive_publisher', None)
                if publisher is not None:
                    assert publisher.stop(timeout=5)
            api._response_store.close()
            api._run_idempotency_store.close()


async def native(entry, method, params):
    from gateway.session_authorities import owner_scope
    from gateway.session_group_replication import dispatch_replication
    def call():
        with owner_scope(entry.authority):
            return dispatch_replication(entry.authority, entry.actor, method, params)
    return await asyncio.to_thread(call)


def create_source_room(source, target):
    from gateway import hosted_rooms
    from gateway.session_authorities import owner_scope
    with owner_scope(target.authority):
        target_id = hosted_rooms.local_authority_gateway_id()
    with owner_scope(source.authority):
        service = source.authority.hosted_room_service
        home_id = hosted_rooms.local_authority_gateway_id()
        service.authorize_room(source.actor.subject, 'room', create=True)
        service.create_room(room_id='room', name='Workshop', members=[
            {'member_id': 'local', 'profile': 'default', 'handle': 'local'},
            {'member_id': 'peer', 'profile': 'default', 'handle': 'peer', 'target': {
                'kind': 'peer', 'installation_id': target_id, 'profile': 'default',
                'peer_id': 'peer', 'capability_digest': 'a' * 64}}])
        hosted_rooms.append_event(service.db_path, room_id='room', event_id='input', kind='message.user',
            actor={'kind': 'user', 'id': source.actor.subject}, payload={'text': 'Shared history'},
            authority_gateway_id=home_id, authority_epoch=1)
    return home_id, target_id


@pytest.mark.asyncio
async def test_ready_runtime_publishes_only_explicit_grants_and_drains(owners, monkeypatch):
    from gateway import hosted_room_replicas, hosted_room_replica_retirement as retirement, hosted_rooms
    from gateway.session_authorities import owner_scope
    from gateway.session_group_controls import dispatch_group_control
    from gateway.session_passive_replication import prepare_passive_publishers, start_passive_publishers
    from gateway.run_runtime import publish_gateway_runtime_ready, drain_gateway_runtime
    import tui_gateway.hosted_room_replication as publisher_module
    source, target, api = owners
    home_id, target_id = create_source_room(source, target)
    monkeypatch.setattr(publisher_module, 'POLL_SECONDS', 0.02)
    app = web.Application(middlewares=[api._make_profile_prefix_middleware()])
    for method, path, handler in api._http_route_table():
        app.router.add_route(method, path, handler)
        app.router.add_route(method, '/p/{profile}' + path, handler)
    async with TestClient(TestServer(app)) as http:
        endpoint = str(http.make_url('')).rstrip('/')
        prepared = await native(source, 'groups.replication.prepare', dict(room_id='room',
            target_install_id=target_id, endpoint=endpoint, enrollment_id='explicit',
            expected_authority={'gateway_id': home_id, 'epoch': 1}))
        enrolled = await native(target, 'groups.replication.enroll', prepared)
        assert enrolled['state'] == 'active'
        invitation = await dispatch_group_control(target, 'groups.peer.invite', dict(
            room_id='room', member_id='peer', home_install_id=home_id, authority_gateway_id=home_id,
            authority_epoch=1, replication=True, work_records=True, passive_only=True))
        from gateway.hosted_room_peer import decode_room_grant, gateway_room_grant_secret
        with owner_scope(target.authority):
            claims = decode_room_grant(gateway_room_grant_secret(), invitation['grant'], permission='replicate')
        assert set(claims['permissions']) == {'status', 'replicate', 'work_records'}
        service = source.authority.hosted_room_service
        monkeypatch.setattr(service.runtime, 'status', lambda: {'running': True})
        await dispatch_group_control(source, 'groups.peer.register', dict(room_id='room', member_id='peer',
            target_url=endpoint, target_profile='default', catalog=invitation['catalog'], grant=invitation['grant']))
        await prepare_passive_publishers(source.runner)
        publisher = source.authority.passive_publisher
        start_passive_publishers(source.runner)
        assert publisher._threads == []
        listener = asyncio.get_running_loop().create_future()
        source.runner.session_api = SimpleNamespace(task=listener)
        service._transport_installed = True
        # The existing coordinator is not part of this passive lifecycle test.
        monkeypatch.setattr(service, 'start', lambda: None)
        publish_gateway_runtime_ready(source.runner)
        first_threads = tuple(publisher._threads)
        publish_gateway_runtime_ready(source.runner)
        assert tuple(publisher._threads) == first_threads
        with owner_scope(target.authority):
            target_db = hosted_rooms.default_db_path()
        for _ in range(300):
            if retirement.current_home_enrollment(service.db_path, room_id='room', target_install_id=target_id)['state'] == 'enrolled':
                try:
                    state = hosted_room_replicas.replica_state(target_db, room_id='room')
                except hosted_room_replicas.ReplicaError:
                    state = {'last_seq': 0}
                routes = publisher.status('room')['routes'] or []
                if (state['last_seq'] >= 1 and state['work_records'].get('scopes')
                        and routes and routes[0]['work_record_status'] == 'acked'):
                    break
            await asyncio.sleep(0.02)
        else:
            pytest.fail(str(publisher.status('room')))
        assert not source.authority.db._read_all('SELECT * FROM session_admissions')
        assert not target.authority.db._read_all('SELECT * FROM session_admissions')
        from gateway.hosted_room_work_records import capture
        record = capture(service.db_path, room_id='room', local_gateway_id=home_id)
        assert state['work_records']['digest'] == record['digest']
        assert state['work_records']['availability'] == record['availability']
        assert state['work_records']['source_loss_safe'] is False
        async def stop_api(handle):
            assert handle.task is listener
            assert not any(thread.is_alive() for thread in first_threads)
        monkeypatch.setattr('gateway.run_api.stop_gateway_api', stop_api)
        await drain_gateway_runtime(source.runner)
        assert source.runner.session_runtime_descriptor['state'] == 'draining'
        assert not any(thread.is_alive() for thread in first_threads)
        assert len(publisher._threads) == len(first_threads)
        status = await native(source, 'groups.replication.status', {'room_id': 'room'})
        assert status['retirement_delivery_enabled'] is False
        assert status['publisher']['source_loss_safe'] is False


@pytest.mark.asyncio
async def test_native_enrollment_owner_and_expected_authority_are_fenced(owners):
    from gateway import hosted_room_replica_retirement as retirement
    from hermes_state_runtime import RuntimeStoreError
    source, target, _ = owners
    home_id, target_id = create_source_room(source, target)
    params = dict(room_id='room', target_install_id=target_id, endpoint='http://127.0.0.1:9999',
                  enrollment_id='test', expected_authority={'gateway_id': home_id, 'epoch': 1})
    for actor in (replace(source.actor, subject='other-owner'), replace(source.actor, capabilities=frozenset({'session:read'})),
                  replace(source.actor, profile_id=target.authority.profile_id)):
        with pytest.raises(RuntimeStoreError):
            await native(SimpleNamespace(authority=source.authority, actor=actor), 'groups.replication.prepare', params)
    with pytest.raises(RuntimeStoreError, match='stale_generation'):
        await native(source, 'groups.replication.prepare', {**params, 'expected_authority': {'gateway_id': home_id, 'epoch': 2}})
    assert retirement.current_home_enrollment(source.authority.db.db_path, room_id='room', target_install_id=target_id) is None
    prepared = await native(source, 'groups.replication.prepare', params)
    assert await native(source, 'groups.replication.prepare', params) == prepared
    assert 'closing_value' not in prepared['enrollment']
    enrolled = await native(target, 'groups.replication.enroll', prepared)
    revoked = await native(target, 'groups.replication.revoke', {'room_id': 'room', 'enrollment_id': enrolled['enrollment_id']})
    assert revoked['state'] == 'revoked'
